from __future__ import annotations  # Eliminates problem with type annotations like list[int]
import os
from datetime import datetime, timezone, timedelta
from typing import Union, List
import json

import numpy as np
import pandas as pd


"""
These functions assume that we have many point-wise predictions produced by individual algorithms
for some feature sets for some labels. The purpose of these functions is to post-process all these
point-wise predictions into some aggregated values which are optimized for trade signal generation.

We define two types of functions:
- Producing aggregated signal (continuous or boolean)
- Measuring quality (performance, precision etc.) for the given aggregation parameters over the input data set with certain point-wise predictions 

Maybe split these two types of functions. The performance functions belong to simulation and grid-search. 

--- OLD ---

GOAL:
Implement parameterized scoring function(s) which could be applied to results of ML predictions 
with point-wise predictions, and compute the score, so that we can do grid search for best 
interval-score computation parameters. 

TODO: function for interval score: how frequently we
- hit an interval (at least one)
- miss an interval (no prediction at all)
- hit non-interval (predict true where it is false at least once)
- miss non-interval (no predictions of true within non-interval)
Our goal is to train best normal model or even several such models which output score (probablity to have top/bottom).
And then we want to apply various aggregation functions to this score or even train (simple) models in an exhaustive way
by maximizing the interval score or at least making better point-wise score

In fact, the ideal score should count
- if there is at least one alarm within an interval
- ignore alarms after the interval (if there was one in the interval)
  we ignore alarms after an interval because we assume that anyway we do not have funds - aftershocks which do not influence the strategy
- if there are no alarms at all before the interval because they break the strategy (early start) 

The main problem here is to define "before" interval and "after" interval.
There are these rules that can be used to determine the division:
- Constant tolerance: absolute, percentage from the length. Problem is that they can overlap if two intervals are close to each other.
- Between two intervals. We essentially estimate (opposite) extreme point - it is somewhere between these two intervals.
  We could also extend this point my turning it to an interval which means estimating the opposite interval 
- Using the opposite interval label. We have trend intervals between them.
  We distinguish up and down trends before and after any interval for computing score. 
  Every interval has its own range, the previous range where we do not want to have alarms, and next range where alarms are allowed (provided that we have alarm within interval).

The scoring function for one-sided prediction should take these arguments:
- point-wise score column (top or bottom)
- interval label column (top or bottom)
- opposite interval label column (bottom or top) to determine after/before ranges
- It returns interval-based confusion matrix: how frequently hit and non-hit interval (true area), how frequently hit/non-hit before interval (false area, note that hit after are ignored only if there was hit in true interval)

The scoring function two-sided prediction:
- point-wise score column (two-sided)
- top interval label column
- bottom interval label column

The scoring function for integral performance. It chooses first alarm (relevant for the state) and computes all differences between them.
It is like finding overall profit if we ware trading. Note that here it is important not to miss extremums.
- Assume some initial state (buying or selling). We could even make two passes for two states and find average
- Find next nearest relevant extremum alarm, compute difference (do the trade) or simply mark this point to compute all differences at the end
- Changing the state and find next relevant nearest alarm by adding the difference or marking the timestamp as buy or sell point.
- Finally compute all differences. If we do it for two initial states then find their average.
  Maybe compute average performance per time (say, per month or per year) in order to be independent of the time range
"""


def aggregate_score(df, score_columns: List[str], signal_column: str, point_threshold, window):
    """
    Add two signal numeric (buy and sell) columns by processing a list of buy and sell point-wise predictions.

    The following operations are applied:
        - find average among all buy and sell columns, respectively
        - find moving average along each individual buy/sell column or/and the two final columns according to window(s)
        - apply threshold to source buy/sell column(s) according to threshold parameter(s) by producing a boolean column

    Notes:
        - Input point-wise scores in buy and sell columns are always positive
    """
    #
    # Average all buy and sell columns
    #
    score_column = df[score_columns].sum(axis=1) / len(score_columns)

    #
    # Apply thresholds (if any)
    #
    if point_threshold:
        score_column = score_column >= point_threshold

    #
    # Moving average
    #
    if isinstance(window, int):
        score_column = score_column.rolling(window, min_periods=window // 2).nanmean()
    elif isinstance(window, float):
        score_column = score_column.ewm(span=window, min_periods=window // 2, adjust=False).nanmean()

    df[signal_column] = score_column

    return score_column


def combined_relative_score(df, buy_column, sell_column, buy_sell_column):
    """
    Combine buy and sell scores and find relative scores.
    For example, if both scores are strong but equal then finally both will be 0
    """

    # proportion to the sum
    high_and_low = df[buy_column] + df[sell_column]
    buy_sell_score = ((df[buy_column] / high_and_low) * 2) - 1.0  # in [-1, +1]
    df[buy_sell_column] = buy_sell_score

    # Final score: abs difference betweem high and low (scaled to [-1,+1] maybe)
    #in_df["score"] = in_df["high"] - in_df["low"]
    from sklearn.preprocessing import StandardScaler
    #in_df["score"] = StandardScaler().fit_transform(in_df["score"])

    return buy_sell_column


def generate_score_high_low(df, feature_sets):
    """
    Add a score column which aggregates different types of scores generated by various algorithms with different options.
    The score is added as a new column and is supposed to be used by the signal generator as the final feature.

    :param df:
    :feature_sets: list of "kline", "futur" etc.
    :return:

    TODO: Refactor by replacing new more generation score aggregation functions which work for any type of label: high-low, top-bot etc.
    """

    if "kline" in feature_sets:
        # high kline: 3 algorithms for all 3 levels
        df["high_k"] = \
            df["high_10_k_gb"] + df["high_10_k_nn"] + df["high_10_k_lc"] + \
            df["high_15_k_gb"] + df["high_15_k_nn"] + df["high_15_k_lc"] + \
            df["high_20_k_gb"] + df["high_20_k_nn"] + df["high_20_k_lc"]
        df["high_k"] /= 9

        # low kline: 3 algorithms for all 3 levels
        df["low_k"] = \
            df["low_10_k_gb"] + df["low_10_k_nn"] + df["low_10_k_lc"] + \
            df["low_15_k_gb"] + df["low_15_k_nn"] + df["low_15_k_lc"] + \
            df["low_20_k_gb"] + df["low_20_k_nn"] + df["low_20_k_lc"]
        df["low_k"] /= 9

        # By algorithm type
        df["high_k_nn"] = (df["high_10_k_nn"] + df["high_15_k_nn"] + df["high_20_k_nn"]) / 3
        df["low_k_nn"] = (df["low_10_k_nn"] + df["low_15_k_nn"] + df["low_20_k_nn"]) / 3

    if "futur" in feature_sets:
        # high futur: 3 algorithms for all 3 levels
        df["high_f"] = \
            df["high_10_f_gb"] + df["high_10_f_nn"] + df["high_10_f_lc"] + \
            df["high_15_f_gb"] + df["high_15_f_nn"] + df["high_15_f_lc"] + \
            df["high_20_f_gb"] + df["high_20_f_nn"] + df["high_20_f_lc"]
        df["high_f"] /= 9

        # low kline: 3 algorithms for all 3 levels
        df["low_f"] = \
            df["low_10_f_gb"] + df["low_10_f_nn"] + df["low_10_f_lc"] + \
            df["low_15_f_gb"] + df["low_15_f_nn"] + df["low_15_f_lc"] + \
            df["low_20_f_gb"] + df["low_20_f_nn"] + df["low_20_f_lc"]
        df["low_f"] /= 9

        # By algorithm type
        df["high_f_nn"] = (df["high_10_f_nn"] + df["high_15_f_nn"] + df["high_20_f_nn"]) / 3
        df["low_f_nn"] = (df["low_10_f_nn"] + df["low_15_f_nn"] + df["low_20_f_nn"]) / 3

    # High and low
    # Both k and f
    #in_df["high"] = (in_df["high_k"] + in_df["high_f"]) / 2
    #in_df["low"] = (in_df["low_k"] + in_df["low_f"]) / 2

    # Only k and all algorithms
    df["high"] = (df["high_k"])
    df["low"] = (df["low_k"])

    # Using one NN algorithm only
    #in_df["high"] = (in_df["high_k_nn"])
    #in_df["low"] = (in_df["low_k_nn"])

    # Final score: proportion to the sum
    high_and_low = df["high"] + df["low"]
    df["score"] = ((df["high"] / high_and_low) * 2) - 1.0  # in [-1, +1]

    # Final score: abs difference betwee high and low (scaled to [-1,+1] maybe)
    #in_df["score"] = in_df["high"] - in_df["low"]
    from sklearn.preprocessing import StandardScaler
    #in_df["score"] = StandardScaler().fit_transform(in_df["score"])

    #in_df["score"] = in_df["score"].rolling(window=10, min_periods=1).apply(np.nanmean)

    return df


#
# Overall scores (trade performance or interval precision)
#

def find_interval_score(df: pd.DataFrame, label_column: str, score_column: str, threshold: float):
    """
    Convert point-wise score/label pairs to interval-wise score/label.

    We assume that for each point there is a score and a boolean label. The score can be a future
    prediction while boolean label is whether this forecast is true. Or the score can be a prediction
    that this is a top/bottom while the label is whether it is indeed so.
    Importantly, the labels are supposed to represent contiguous intervals because the algorithm
    will output results for them by aggregating scores within these intervals.

    The output is a data frame with one row per contiguous interval. The intervals are interleaving
    like true, false, true, false etc. Accordingly, there is one label column which takes these
    values true, false etc. The score column for each interval is computed by using these rules:
    - for true interval: true (positive) if there is at least one point with score higher than the threshold
    - for true interval: false (positive) if all points are lower than the threshold
    - for false interval: true (negative) if all points are lower than the threshold
    - for false interval: false (negative) if there is at least one (wrong) points with the score higher than the thresond
    Essentially, we need only one boolean "all lower" function

    The input point-wise score is typically aggregated by applying some kind of rolling aggregation
    but it is performed separately.

    The function is supposed to be used for scoring during hyper-parameter search.
    We can search in level, tolerance, threshold, aggregation hyper-paraemters (no forecasting parameters).
    Or we can also search through various ML forecasting hyper-parameters like horizon etc.
    In any case, after we selected hyper-parameters, we apply interval selection, score aggregation,
    then apply this function, and finally computing the interval-wise score.

    Input data frame is supposed to be sorted (important for the algorithm of finding contiguous intervals).
    """

    #
    # Count all intervals by finding them as groups of points. Input is a boolean column with interleaving true-false
    # Mark true intervals (extremum) and false intervals (non-extremum)
    #

    # Find indexes with transfer from 0 to 1 (+1) and from 1 to 0 (-1)
    out = df[label_column].diff()
    out.iloc[0] = False  # Assume no change
    out = out.astype(int)

    # Find groups (intervals, starts-stops) and assign true-false label to them
    interval_no_column = 'interval_no'
    df[interval_no_column] = out.cumsum()

    #
    # For each group (with true-false label), compute their interval-wise score (using all or none principle)
    #

    # First, compute "score lower" (it will be used during interval-based aggregation)
    df[score_column + '_greater_than_threshold'] = (df[score_column] >= threshold)

    # Interval objects
    by_interval = df.groupby(interval_no_column)

    # Find interval label
    # Either 0 (all false) or 1 (at least one true - but must be all true)
    interval_label = by_interval[label_column].max()

    # Apply "all lower" function to each interval scores.
    # Either 0 (all lower) or 1 (at least one higher)
    interval_score = by_interval[score_column + '_greater_than_threshold'].max()
    interval_score.name = score_column

    # Compute into output
    interval_df = pd.concat([interval_label, interval_score], axis=1)
    interval_df = interval_df.reset_index(drop=False)

    return interval_df


def performance_score(df, sell_signal_column, buy_signal_column, price_column):
    """
    top_score_column: boolean, true if top is reached - sell signal
    bot_score_column: boolean, true if bottom is reached - buy signal
    price_column: numeric price for computing profit

    return performance: tuple, long and short performance as a sum of differences between two transactions

    The functions switches the mode and searches for the very first signal of the opposite score.
    When found, it again switches the mode and searches for the very first signal of the opposite score.

    Essentially, it is one pass of trade simulation with concrete parameters.
    """
    is_buy_mode = True

    performance_long = 0
    long_count = 0
    long_profitable = 0
    longs = list()

    performance_short = 0
    short_count = 0
    short_profitable = 0
    shorts = list()

    # The order of columns is important for itertuples
    df = df[[sell_signal_column, buy_signal_column, price_column]]
    for (index, top_score, bot_score, price) in df.itertuples(name=None):
        if is_buy_mode:
            # Check if minimum price
            if bot_score:
                profit = longs[-1][2] - price if len(longs) > 0 else 0
                performance_short += profit
                short_count += 1
                if profit > 0:
                    short_profitable += 1
                shorts.append((index, is_buy_mode, price, profit))  # Bought
                is_buy_mode = False
        else:
            # Check if maximum price
            if top_score:
                profit = price - shorts[-1][2] if len(shorts) > 0 else 0
                performance_long += profit
                long_count += 1
                if profit > 0:
                    long_profitable += 1
                longs.append((index, is_buy_mode, price, profit))  # Sold
                is_buy_mode = True

    return performance_long, performance_short, long_count, short_count, long_profitable, short_profitable, longs, shorts


# NOT USED
def generate_signals(df, models: dict):
    """
    Use predicted labels in the data frame to decide whether to buy or sell.
    Use rule-based approach by comparing the predicted scores with some thresholds.
    The decision is made for the last row only but we can use also previous data.

    TODO: In future, values could be functions which return signal 1 or 0 when applied to a row

    :param df: data frame with features which will be used to generate signals
    :param models: dict where key is a signal name which is also an output column name and value a dict of parameters of the model
    :return: A number of binary columns will be added each corresponding to one signal and having same name
    """

    # Define one function for each signal type.
    # A function applies a predicates by using the provided parameters and qualifies this row as true or false
    # TODO: Access to model parameters and row has to be rubust and use default values (use get instead of [])

    def all_higher_fn(row, model):
        keys = model.keys()
        for field, value in model.items():
            if row.get(field) >= value:
                continue
            else:
                return 0
        return 1

    def all_lower_fn(row, model):
        keys = model.keys()
        for field, value in model.items():
            if row.get(field) <= value:
                continue
            else:
                return 0
        return 1

    for signal, model in models.items():
        # Choose function which implements (knows how to generate) this signal
        fn = None
        if signal == "buy":
            fn = all_higher_fn
        elif signal == "sell":
            fn = all_lower_fn
        else:
            print("ERROR: Wrong use. Unexpected signal name.")

        # Model will be passed as the second argument (the first one is the row)
        df[signal] = df.apply(fn, axis=1, args=[model])

    return models.keys()


if __name__ == '__main__':
    data = [
        [1, 2, 3, 4],
        [1, 2, 3, 4],
        [1, 2, 3, 4]
    ]

    df = pd.DataFrame(data, columns=["price_column", "aaa", "bot_score_column", "top_score_column"])

    performance_score(df, "top_score_column", "bot_score_column", "price_column", start=123)
    pass
