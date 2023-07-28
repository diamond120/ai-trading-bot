import os
import sys
import importlib
from datetime import datetime, timezone, timedelta
from typing import Union
import json

import numpy as np
import pandas as pd

import scipy.stats as stats

from common.utils import *
from common.feature_generation_rolling_agg import *
from common.feature_generation_rolling_agg import _aggregate_last_rows

"""
Feature generators. 
A feature generator knows how to generate features from its delcarative specification in the config file.
"""


def generate_features_tsfresh(df, config: dict, last_rows: int = 0):
    """
    This feature generator relies on tsfresh functions.

    tsfresh depends on matrixprofile for which binaries are not available for many versions.
    Therefore, the use of tsfresh may require Python 3.8
    """
    # It is imported here in order to avoid installation of tsfresh if it is not used
    import tsfresh.feature_extraction.feature_calculators as tsf

    # Transform str/list and list to dict with argument names as keys and column names as values
    column_names = config.get('columns')
    if not column_names:
        raise ValueError(f"No input column for feature generator 'stats': {column_names}")

    if isinstance(column_names, str):
        column_name = column_names
    elif isinstance(column_names, list):
        column_name = column_names[0]
    elif isinstance(column_names, dict):
        column_name = next(iter(column_names.values()))
    else:
        raise ValueError(f"Columns are provided as a string, list or dict. Wrong type: {type(column_names)}")

    column = df[column_name].interpolate()

    windows = config.get('windows')
    if not isinstance(windows, list):
        windows = [windows]

    features = []
    for w in windows:
        ro = column.rolling(window=w, min_periods=max(1, w // 2))

        #
        # Statistics
        #
        feature_name = column_name + "_skewness_" + str(w)
        if not last_rows:
            df[feature_name] = ro.apply(tsf.skewness, raw=True)
        else:
            df[feature_name] = _aggregate_last_rows(column, w, last_rows, tsf.skewness)  # OR skew (but it computes different values)
        features.append(feature_name)

        feature_name = column_name + "_kurtosis_" + str(w)
        if not last_rows:
            df[feature_name] = ro.apply(tsf.kurtosis, raw=True)
        else:
            df[feature_name] = _aggregate_last_rows(column, w, last_rows, tsf.kurtosis)  # OR kurtosis
        features.append(feature_name)

        # count_above_mean, benford_correlation, mean_changes
        feature_name = column_name + "_msdc_" + str(w)
        if not last_rows:
            df[feature_name] = ro.apply(tsf.mean_second_derivative_central, raw=True)
        else:
            df[feature_name] = _aggregate_last_rows(column, w, last_rows, tsf.mean_second_derivative_central)
        features.append(feature_name)

        #
        # Counts
        # first/last_location_of_maximum/minimum
        #
        feature_name = column_name + "_lsbm_" + str(w)
        if not last_rows:
            df[feature_name] = ro.apply(tsf.longest_strike_below_mean, raw=True)
        else:
            df[feature_name] = _aggregate_last_rows(column, w, last_rows, tsf.longest_strike_below_mean)
        features.append(feature_name)

        feature_name = column_name + "_fmax_" + str(w)
        if not last_rows:
            df[feature_name] = ro.apply(tsf.first_location_of_maximum, raw=True)
        else:
            df[feature_name] = _aggregate_last_rows(column, w, last_rows, tsf.first_location_of_maximum)
        features.append(feature_name)

    return features


def generate_features_talib(df, config: dict, last_rows: int = 0):
    """
    Apply TA functions from talib according to the specified configuration parameters.

    config = {
        "parameters": {"relative": True, "realtive_to_last": True, "percentage": True},
        "columns": ["close"],
        "functions": ["SMA"],
        "windows": [2, 3],  # If numbers, then to argument timeperiod. If dict, then
        "args": {},  # Pass to the function as additional arguments
        "names": "my_output",  # How the output feature(s) will be named
    }

    talib is very sensitive to NaN values so that one NaN somewhere in the input series can produce
    NaN in output even if formally it does not influence it. For example, one NaN in the beginning of
    input series will produce NaN of SMA in the end with small window like 2.
    Therefore, NaN should be completely removed to get meaningful results (even if they formally do
    not influence the result values you are interested in).

    # TODO: Add math functions with two (or more) columns passed to certain arguments, no windows or parameters
    #   two arguments: real0, real1. Alternative, pass as a list (no argument names)
    # TODO: currently it works for only one window per function. Some talib functions may take 2 or more windows (similar to taking 2 input columns)

    # TODO: If window list is a dict, then use key as argument name for this call
    # TODO: If columns list is a dict, then key is argment to ta function, and value is column name (if ta function takes some custom arguments)
    # TODO: args config parameters - pass in unchanged for to each call
    # TODO: Currently works only for one column (second ignored). Make it work for two and more input columns
    # TODO: add parameter: use_differences if true then compute differences first, another parameter is using log=2,10 etc. (or some conventional)

    :param config:
    :return:
    """
    rel = config.get('parameters', {}).get('rel', False)
    # If true, then relative values are multiplied by 100
    percentage = config.get('parameters', {}).get('percentage', False)
    # If true, then logarithm is applied to the result
    log = config.get('parameters', {}).get('log', False)

    #
    # talib module where all ta functions are defined. we use it below to resolve TA function names
    #
    mod_name = "talib"  # Functions are applied to a (rolling) series of windows
    talib_mod = sys.modules.get(mod_name)  # Try to load
    if talib_mod is None:  # If not yet imported
        try:
            talib_mod = importlib.import_module(mod_name)  # Try to import
        except Exception as e:
            raise ValueError(f"Cannot import module 'talib'. Check if talib is installed correctly")

    mod_name = "talib.stream"  # Functions which are applied to single window and return one value
    talib_mod_stream = sys.modules.get(mod_name)  # Try to load
    if talib_mod_stream is None:  # If not yet imported
        try:
            talib_mod_stream = importlib.import_module(mod_name)  # Try to import
        except Exception as e:
            raise ValueError(f"Cannot import module 'talib'. Check if talib is installed correctly")

    #
    # Process configuration parameters and prepare all needed for feature generation
    #

    # Transform str/list and list to dict with argument names as keys and column names as values
    column_names = config.get('columns')
    if isinstance(column_names, str):
        column_names = {'real': column_names}  # Single default input series
    elif isinstance(column_names, list) and len(column_names) == 1:
        column_names = {'real': column_names[0]}  # Single default input series
    elif isinstance(column_names, list):
        column_names = {f'real{i}': col for i, col in enumerate(column_names)}  # Multiple default input series
    elif isinstance(column_names, dict):
        pass  # Do nothing
    else:
        raise ValueError(f"Columns are provided as a string, list or dict. Wrong type: {type(column_names)}")

    # For each key, resolve name and interpolate data
    # Interpolate (we should always do it because one NaN in input can produce all NaNs in output)
    columns = {arg: df[col_name].interpolate() for arg, col_name in column_names.items()}

    col_out_names = "_".join(column_names.values())  # Join all column names

    func_names = config.get('functions')
    if not isinstance(func_names, list):
        func_names = [func_names]

    windows = config.get('windows')
    if not isinstance(windows, list):
        windows = [windows]

    names = config.get('names')

    #
    # For each function, make several calls for each window size
    #
    outs = []
    features = []
    for func_name in func_names:
        fn_outs = []
        fn_out_names = []
        # Now this function will be called for each window as a parameter
        for j, w in enumerate(windows):

            #
            # Prepare arguments
            #

            # Only aggregation functions have window argument (arithmetic functions do not have it)

            if not (last_rows and w):  # The function will be executed in a rolling manner and applied to rolling windows
                try:
                    fn = getattr(talib_mod, func_name)  # Resolve function name
                except AttributeError as e:
                    raise ValueError(f"Cannot resolve talib function name '{func_name}'. Check the (existence of) name of the function")

                args = columns.copy()
                if w:
                    args['timeperiod'] = w
                if w == 1 and len(columns) == 1:  # For window 1 use the original values (because talib fails to do this)
                    out = next(iter(columns.values()))
                else:
                    out = fn(**args)
            else:  # Compute the specified number of single values for the manually prepared windows
                try:
                    fn = getattr(talib_mod_stream, func_name)  # Resolve function name
                except AttributeError as e:
                    raise ValueError(f"Cannot resolve talib.stream function name '{func_name}'. Check the (existence of) name of the function")

                # Here fn (function) is a different function from a different module (this function is applied to a single window rather than to rolling windows)
                out_values = []
                for r in range(last_rows):
                    # Remove r elements from the end
                    # Note that we do not remove elements from the start so the length is limited from one side only
                    args = {k: v.iloc[:len(v)-r] for k, v in columns.items()}
                    if w:
                        args['timeperiod'] = w

                    if w == 1 and len(columns) == 1:  # For window 1 use the original values (because talib fails to do this)
                        col = next(iter(columns.values()))
                        out_val = col.iloc[-r-1]
                    else:
                        out_val = fn(**args)
                    out_values.append(out_val)

                # Then these values are transformed to a series
                out = pd.Series(data=np.nan, index=df.index, dtype=float)
                out.iloc[-last_rows:] = list(reversed(out_values))  # Assign values to the last elements

            # Name of the output column
            # Now combin[e: columnnames + functionname + [if prefix null window [i] | elif prefix str + window[i] | else if list prefix[i]]
            if not w:
                if not names:
                    out_name = f"{col_out_names}_{func_name}"
                elif isinstance(names, str):
                    out_name = names
                elif isinstance(names, list):
                    out_name = names[j]  # Should not happen
            else:
                out_name = f"{col_out_names}_{func_name}_"
                win_name = str(w)
                if not names:
                    out_name = out_name + win_name
                elif isinstance(names, str):
                    out_name = out_name + names + "_" + win_name
                elif isinstance(names, list):
                    out_name = out_name + names[j]

            fn_out_names.append(out_name)

            out.name = out_name

            fn_outs.append(out)

        # Convert to relative values and percentage (except for the last output)
        rel_outs = []
        for i in range(len(fn_outs) - 1):
            if not rel:
                rel_out = fn_outs[i]  # No change
            elif rel == "next" or rel == "last":
                if rel == "next":
                    rel_out = fn_outs[i] / fn_outs[i + 1]  # Relative to next
                elif rel == "last":
                    rel_out = fn_outs[i] / fn_outs[-1]  # Relative to last

                if percentage:
                    rel_out = rel_out * 100.0
            else:
                raise ValueError(f"Unknown value of feature generator parameter {rel=}")

            rel_out.name = fn_outs[i].name
            rel_outs.append(rel_out)

        rel_outs.append(fn_outs[-1])  # Last window as added always unchanged

        features.extend(fn_out_names)

        outs.extend(rel_outs)

    for out in outs:
        df[out.name] = np.log(out) if log else out

    return features


def generate_features_itbstats(df, config: dict, last_rows: int = 0):
    """
    Statistical and various other features.

    In particular, it is intended to replace functions from tsfresh as well as implement
    functions which are not available in other libraries like volume weighted close price.

    Currently applied to only one input column.
    Currently generates all functions - 'functions' parameter is not used.
    """

    # Transform str/list and list to dict with argument names as keys and column names as values
    column_names = config.get('columns')
    if not column_names:
        raise ValueError(f"No input column for feature generator 'stats': {column_names}")

    if isinstance(column_names, str):
        column_name = column_names
    elif isinstance(column_names, list):
        column_name = column_names[0]
    elif isinstance(column_names, dict):
        column_name = next(iter(column_names.values()))
    else:
        raise ValueError(f"Columns are provided as a string, list or dict. Wrong type: {type(column_names)}")

    column = df[column_name].interpolate()

    windows = config.get('windows')
    if not isinstance(windows, list):
        windows = [windows]

    # TODO: use custom names instead of automatically generated ones
    names = config.get('names')

    features = []
    for w in windows:
        ro = column.rolling(window=w, min_periods=max(1, w // 2))

        #
        # Statistics
        #
        feature_name = column_name + "_skew_" + str(w)
        if not last_rows:
            df[feature_name] = ro.apply(stats.skew, raw=True)
        else:
            df[feature_name] = _aggregate_last_rows(column, w, last_rows, stats.skew)
        features.append(feature_name)

        feature_name = column_name + "_kurtosis_" + str(w)
        if not last_rows:
            df[feature_name] = ro.apply(stats.kurtosis, raw=True)
        else:
            df[feature_name] = _aggregate_last_rows(column, w, last_rows, stats.kurtosis)
        features.append(feature_name)

        #feature_name = column_name + "_msdc_" + str(w)
        #if not last_rows:
        #    df[feature_name] = ro.apply(tsf.mean_second_derivative_central, raw=True)
        #else:
        #    df[feature_name] = _aggregate_last_rows(column, w, last_rows, tsf.mean_second_derivative_central)
        #features.append(feature_name)

        #
        # Counts
        # first/last_location_of_maximum/minimum
        #
        #feature_name = column_name + "_lsbm_" + str(w)
        #if not last_rows:
        #    df[feature_name] = ro.apply(tsf.longest_strike_below_mean, raw=True)
        #else:
        #    df[feature_name] = _aggregate_last_rows(column, w, last_rows, tsf.longest_strike_below_mean)
        #features.append(feature_name)

        #feature_name = column_name + "_fmax_" + str(w)
        #if not last_rows:
        #    df[feature_name] = ro.apply(tsf.first_location_of_maximum, raw=True)
        #else:
        #    df[feature_name] = _aggregate_last_rows(column, w, last_rows, tsf.first_location_of_maximum)
        #features.append(feature_name)

    return features


def generate_features_itblib(df, config: dict, last_rows: int = 0):
    """
    Generate derived features by adding them as new columns to the data frame.
    It is important that the same parameters are used for both training and prediction.

    Most features compute rolling aggregation. However, instead of absolute values, the difference
    of this rolling aggregation to the (longer) base rolling aggregation is computed.

    The window sizes are used for encoding feature/column names and might look like 'close_120'
    for average close price for the last 120 minutes (relative to the average base price).
    The column names are needed when preparing data for training or prediction.
    The easiest way to get them is to return from this function and copy and the
    corresponding config attribute.
    """
    use_differences = config.get('use_differences', True)
    base_window = config.get('base_window', True)
    windows = config.get('windows', True)
    functions = config.get('functions', True)

    features = []
    to_drop = []

    if use_differences:
        df['close'] = to_diff(df['close'])
        df['volume'] = to_diff(df['volume'])
        df['trades'] = to_diff(df['trades'])

    # close rolling mean. format: 'close_<window>'
    if not functions or "close_WMA" in functions:
        weight_column_name = 'volume'  # None: no weighting; 'volume': volume average
        to_drop += add_past_weighted_aggregations(df, 'close', weight_column_name, np.nanmean, base_window, suffix='', last_rows=last_rows)  # Base column
        features += add_past_weighted_aggregations(df, 'close', weight_column_name, np.nanmean, windows, '', to_drop[-1], 100.0, last_rows=last_rows)

    # close rolling std. format: 'close_std_<window>'
    if not functions or "close_STD" in functions:
        to_drop += add_past_aggregations(df, 'close', np.nanstd, base_window, last_rows=last_rows)  # Base column
        features += add_past_aggregations(df, 'close', np.nanstd, windows, '_std', to_drop[-1], 100.0, last_rows=last_rows)

    # volume rolling mean. format: 'volume_<window>'
    if not functions or "volume_SMA" in functions:
        to_drop += add_past_aggregations(df, 'volume', np.nanmean, base_window, suffix='', last_rows=last_rows)  # Base column
        features += add_past_aggregations(df, 'volume', np.nanmean, windows, '', to_drop[-1], 100.0, last_rows=last_rows)

    # Span: high-low difference. format: 'span_<window>'
    if not functions or "span_SMA" in functions:
        df['span'] = df['high'] - df['low']
        to_drop.append('span')
        to_drop += add_past_aggregations(df, 'span', np.nanmean, base_window, suffix='', last_rows=last_rows)  # Base column
        features += add_past_aggregations(df, 'span', np.nanmean, windows, '', to_drop[-1], 100.0, last_rows=last_rows)

    # Number of trades format: 'trades_<window>'
    if not functions or "trades_SMA" in functions:
        to_drop += add_past_aggregations(df, 'trades', np.nanmean, base_window, suffix='', last_rows=last_rows)  # Base column
        features += add_past_aggregations(df, 'trades', np.nanmean, windows, '', to_drop[-1], 100.0, last_rows=last_rows)

    # tb_base_av / volume varies around 0.5 in base currency. format: 'tb_base_<window>>'
    if not functions or "tb_base_SMA" in functions:
        df['tb_base'] = df['tb_base_av'] / df['volume']
        to_drop.append('tb_base')
        to_drop += add_past_aggregations(df, 'tb_base', np.nanmean, base_window, suffix='', last_rows=last_rows)  # Base column
        features += add_past_aggregations(df, 'tb_base', np.nanmean, windows, '', to_drop[-1], 100.0, last_rows=last_rows)

    # UPDATE: do not generate, because very high correction (0.99999) with tb_base
    # tb_quote_av / quote_av varies around 0.5 in quote currency. format: 'tb_quote_<window>>'
    #df['tb_quote'] = df['tb_quote_av'] / df['quote_av']
    #to_drop.append('tb_quote')
    #to_drop += add_past_aggregations(df, 'tb_quote', np.nanmean, base_window, suffix='', last_rows=last_rows)  # Base column
    #features += add_past_aggregations(df, 'tb_quote', np.nanmean, windows, '', to_drop[-1], 100.0, last_rows=last_rows)

    # Area over and under latest close price
    if not functions or "close_AREA" in functions:
        features += add_area_ratio(df, is_future=False, column_name="close", windows=windows, suffix = "_area", last_rows=last_rows)

    # Linear trend
    if not functions or "close_SLOPE" in functions:
        features += add_linear_trends(df, is_future=False, column_name="close", windows=windows, suffix="_trend", last_rows=last_rows)
    if not functions or "volume_SLOPE" in functions:
        features += add_linear_trends(df, is_future=False, column_name="volume", windows=windows, suffix="_trend", last_rows=last_rows)

    df.drop(columns=to_drop, inplace=True)

    return features


def generate_features_depth(df, use_differences=False):
    """
    Generate derived features from depth data.
    Original features:
    - gap, price,
    - bids_1,asks_1,
    - bids_2,asks_2,
    - bids_5,asks_5,
    - bids_10,asks_10,
    - bids_20,asks_20

    Features (33):
    gap_2,gap_5,gap_10,
    bids_1_2,bids_1_5,bids_1_10, asks_1_2,asks_1_5,asks_1_10,
    bids_2_2,bids_2_5,bids_2_10, asks_2_2,asks_2_5,asks_2_10,
    bids_5_2,bids_5_5,bids_5_10, asks_5_2,asks_5_5,asks_5_10,
    bids_10_2,bids_10_5,bids_10_10, asks_10_2,asks_10_5,asks_10_10,
    bids_20_2,bids_20_5,bids_20_10, asks_20_2,asks_20_5,asks_20_10,
    """
    # Parameters of moving averages
    windows = [2, 5, 10]
    base_window = 30

    features = []
    to_drop = []

    # gap mean
    to_drop += add_past_aggregations(df, 'gap', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'gap', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['gap_2', 'gap_5', 'gap_10']


    # bids_1 mean
    to_drop += add_past_aggregations(df, 'bids_1', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'bids_1', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['bids_1_2', 'bids_1_5', 'bids_1_10']
    # asks_1 mean
    to_drop += add_past_aggregations(df, 'asks_1', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'asks_1', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['asks_1_2', 'asks_1_5', 'asks_1_10']


    # bids_2 mean
    to_drop += add_past_aggregations(df, 'bids_2', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'bids_2', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['bids_2_2', 'bids_2_5', 'bids_2_10']
    # asks_2 mean
    to_drop += add_past_aggregations(df, 'asks_2', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'asks_2', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['asks_2_2', 'asks_2_5', 'asks_2_10']


    # bids_5 mean
    to_drop += add_past_aggregations(df, 'bids_5', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'bids_5', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['bids_5_2', 'bids_5_5', 'bids_5_10']
    # asks_5 mean
    to_drop += add_past_aggregations(df, 'asks_5', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'asks_5', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['asks_5_2', 'asks_5_5', 'asks_5_10']


    # bids_10 mean
    to_drop += add_past_aggregations(df, 'bids_10', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'bids_10', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['bids_10_2', 'bids_10_5', 'bids_10_10']
    # asks_10 mean
    to_drop += add_past_aggregations(df, 'asks_10', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'asks_10', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['asks_10_2', 'asks_10_5', 'asks_10_10']


    # bids_20 mean
    to_drop += add_past_aggregations(df, 'bids_20', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'bids_20', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['bids_20_2', 'bids_20_5', 'bids_20_10']
    # asks_20 mean
    to_drop += add_past_aggregations(df, 'asks_20', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'asks_20', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['asks_20_2', 'asks_20_5', 'asks_20_10']


    df.drop(columns=to_drop, inplace=True)

    return features


def add_threshold_feature(df, column_name: str, thresholds: list, out_names: list):
    """

    :param df:
    :param column_name: Column with values to compare with the thresholds
    :param thresholds: List of thresholds. For each of them an output column will be generated
    :param out_names: List of output column names (same length as thresholds)
    :return: List of output column names
    """

    for i, threshold in enumerate(thresholds):
        out_name = out_names[i]
        if threshold > 0.0:  # Max high
            if abs(threshold) >= 0.75:  # Large threshold
                df[out_name] = df[column_name] >= threshold  # At least one high is greater than the threshold
            else:  # Small threshold
                df[out_name] = df[column_name] <= threshold  # All highs are less than the threshold
        else:  # Min low
            if abs(threshold) >= 0.75:  # Large negative threshold
                df[out_name] = df[column_name] <= threshold  # At least one low is less than the (negative) threshold
            else:  # Small threshold
                df[out_name] = df[column_name] >= threshold  # All lows are greater than the (negative) threshold

    return out_names


def klines_to_df(klines: list):
    """
    Convert a list of klines to a data frame.
    """
    columns = [
        'timestamp',
        'open', 'high', 'low', 'close', 'volume',
        'close_time',
        'quote_av', 'trades', 'tb_base_av', 'tb_quote_av',
        'ignore'
    ]

    df = pd.DataFrame(klines, columns=columns)

    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')

    df["open"] = pd.to_numeric(df["open"])
    df["high"] = pd.to_numeric(df["high"])
    df["low"] = pd.to_numeric(df["low"])
    df["close"] = pd.to_numeric(df["close"])
    df["volume"] = pd.to_numeric(df["volume"])

    df["quote_av"] = pd.to_numeric(df["quote_av"])
    df["trades"] = pd.to_numeric(df["trades"])
    df["tb_base_av"] = pd.to_numeric(df["tb_base_av"])
    df["tb_quote_av"] = pd.to_numeric(df["tb_quote_av"])

    if "timestamp" in df.columns:
        df.set_index('timestamp', inplace=True)

    return df


if __name__ == "__main__":
    pass
