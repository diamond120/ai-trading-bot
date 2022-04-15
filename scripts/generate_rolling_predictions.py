import sys
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Union
import json
import pickle
from concurrent.futures import ProcessPoolExecutor
import click

import numpy as np
import pandas as pd

from service.App import *
from common.utils import *
from common.classifiers import *
from common.feature_generation import *
from scripts.hyper_parameters import *

"""
Generate label predictions for the whole input feature matrix by iteratively training models using historic data and predicting labels for some future horizon.
The main parameter is the step of iteration, that is, the future horizon for prediction.
As usual, we can specify past history length used to train a model.
The output file will store predicted labels in addition to all input columns (generated features and true labels).
This file is intended for training signal models (by simulating trade process and computing overall performance for some long period).
The output predicted labels will cover shorter period of time because we need some relatively long history to train the very first model.
"""

#
# Parameters
#
class P:
    # Each one is a separate procedure with its algorithm and (expected) input features
    # Leave only what we want to generate (say, only klines for debug purposes)
    feature_sets = ["kline", ]  # "futur"
    algorithms = ["nn"]  # gb, nn, lc

    start_index = 0
    end_index = None

    in_file_suffix = "matrix"
    predict_file_suffix = "predictions-rolling"

    # How much data we want to use for training
    kline_train_length = int(1.5 * 525_600)  # 1.5 * 525_600
    futur_train_length = int(4 * 43_800)

    # First row for starting predictions: "2020-02-01 00:00:00" - minimum start for futures
    prediction_start_str = "2020-02-01 00:00:00"
    # How frequently re-train models: 1 day: 1_440 = 60 * 24, one week: 10_080
    prediction_length = 2*7*1440
    prediction_count = 56  # How many prediction steps. If None or 0, then from prediction start till the data end. Use: https://www.timeanddate.com/date/duration.html

    use_multiprocessing = True
    max_workers = 8  # None means number of processors


#
# Main
#

@click.command()
@click.option('--config_file', '-c', type=click.Path(), default='', help='Configuration file name')
def main(config_file):
    load_config(config_file)

    label_horizon = App.config["label_horizon"]  # Labels are generated using this number of steps ahead
    labels = App.config.get("labels")

    features_kline = App.config.get("features_kline")
    features_futur = App.config.get("features_futur")
    features_depth = App.config.get("features_depth")

    freq = "1m"
    symbol = App.config["symbol"]
    data_path = Path(App.config["data_folder"])
    if not data_path.is_dir():
        print(f"Data folder does not exist: {data_path}")
        return

    #
    # Load feature matrix
    #
    start_dt = datetime.now()

    in_file_name = f"{symbol}-{P.in_file_suffix}.csv"
    in_path = data_path / in_file_name
    if not in_path.exists():
        print(f"ERROR: Input file does not exist: {in_path}")
        return

    print(f"Loading feature matrix from input file: {in_path}")
    df = None
    if in_file_name.endswith(".csv"):
        df = pd.read_csv(in_path, parse_dates=['timestamp'])
    elif in_file_name.endswith(".parquet"):
        df = pd.read_parquet(in_path)
    elif in_file_name.endswith(".pickle"):
        df = pd.read_pickle(in_path)
    else:
        print(f"ERROR: Unknown input file extension. Only csv and parquet are supported.")

    print(f"Feature matrix loaded. Length: {len(df)}. Width: {len(df.columns)}")

    #
    # Limit length according to parameters start_index end_index
    #
    df = df.iloc[P.start_index:P.end_index]
    df = df.reset_index()

    # Spot and futures have different available histories. If we drop nans in all of them, then we get a very short data frame (corresponding to futureus which have little data)
    # So we do not drop data here but rather when we select necessary input features
    # Nans result in constant accuracy and nan loss. MissingValues procedure does not work and produces exceptions
    pd.set_option('use_inf_as_na', True)
    #in_df = in_df.dropna(subset=labels)
    df = df.reset_index(drop=True)  # We must reset index after removing rows to remove gaps

    prediction_start = find_index(df, P.prediction_start_str)
    print(f"Start index: {prediction_start}")

    #
    # Limit columns
    #
    for label in labels:
        df[label] = df[label].astype(int)  # "category" NN does not work without this

    # Select necessary features and label
    out_columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time']
    all_features = []
    if "kline" in P.feature_sets:
        all_features += features_kline
    if "futur" in P.feature_sets:
        all_features += features_futur
    all_features += labels
    df = df[all_features + out_columns]

    #
    # Rolling train-predict loop
    #
    stride = P.prediction_length
    steps = P.prediction_count
    if not steps:
        # Use all available rest data (from the prediction start to the dataset end)
        steps = (len(df) - prediction_start) // stride
    if len(df) - prediction_start < steps * stride:
        raise ValueError(f"Number of steps {steps} is too high (not enough data after start). Data available for prediction: {len(df) - prediction_start}. Data to be predicted: {steps * stride} ")

    print(f"Starting rolling predict loop with {steps} steps. Each step with {stride} horizon...")

    # Result rows. Here store only rows for which we make predictions
    labels_hat_df = pd.DataFrame()

    for step in range(steps):

        print(f"\n===>>> Start step {step}/{steps}")

        # Predict data

        predict_start = prediction_start + (step * stride)
        predict_end = predict_start + stride

        predict_df = df.iloc[predict_start:predict_end]  # We assume that iloc is equal to index
        # predict_df = predict_df.dropna(subset=features)  # Nans will be droped by the algorithms themselves

        # Here we will collect predicted columns
        predict_labels_df = pd.DataFrame(index=predict_df.index)

        # ===
        # kline feature set
        # ===
        if "kline" in P.feature_sets:
            features = features_kline
            name_tag = "_k_"
            train_length = P.kline_train_length

            print(f"Start training 'kline' package with {len(features)} features, name tag {name_tag}', and train length {train_length}")

            # Predict data

            df_X_test = predict_df[features]
            #df_y_test = predict_df[predict_label]  # It will be set in the loop over labels

            # Train data

            # We exclude recent objects from training, because they do not have labels yet - the labels are in future
            # In real (stream) data, we will have null labels for recent objects. During simulation, labels are available and hence we need to ignore/exclude them manually
            train_end = predict_start - label_horizon - 1
            train_start = train_end - train_length
            train_start = 0 if train_start < 0 else train_start

            train_df = df.iloc[int(train_start):int(train_end)]  # We assume that iloc is equal to index
            train_df = train_df.dropna(subset=features)

            df_X = train_df[features]
            #df_y = train_df[predict_label]  # It will be set in the loop over labels

            print(f"Train range: [{train_start}, {train_end}]={train_end-train_start}. Prediction range: [{predict_start}, {predict_end}]={predict_end-predict_start}. ")

            for label in labels:  # Train-predict different labels (and algorithms) using same X
                df_y = train_df[label]
                df_y_test = predict_df[label]

                if P.use_multiprocessing:
                    # Submit train-predict algorithms to the pool
                    execution_results = dict()
                    with ProcessPoolExecutor(max_workers=P.max_workers) as executor:
                        # --- GB
                        if "gb" in P.algorithms:
                            score_column_name = label + name_tag + "gb"
                            execution_results[score_column_name] = executor.submit(train_predict_gb, df_X, df_y, df_X_test, get_model("gb"))

                        # --- NN
                        if "nn" in P.algorithms:
                            score_column_name = label + name_tag + "nn"
                            execution_results[score_column_name] = executor.submit(train_predict_nn, df_X, df_y, df_X_test, get_model("nn"))

                        # --- LC
                        if "lc" in P.algorithms:
                            score_column_name = label + name_tag + "lc"
                            execution_results[score_column_name] = executor.submit(train_predict_lc, df_X, df_y, df_X_test, get_model("lc"))

                    # Process the results as the tasks are finished
                    for score_column_name, future in execution_results.items():
                        predict_labels_df[score_column_name] = future.result()
                        if future.exception():
                            print(f"Exception while train-predict {score_column_name}.")
                            return
                else:  # No multiprocessing - sequential execution
                    # --- GB
                    if "gb" in P.algorithms:
                        y_hat = train_predict_gb(df_X, df_y, df_X_test, model_config=get_model("gb"))
                        score_column_name = label + name_tag + "gb"
                        predict_labels_df[score_column_name] = y_hat

                    # --- NN
                    if "nn" in P.algorithms:
                        y_hat = train_predict_nn(df_X, df_y, df_X_test, model_config=get_model("nn"))
                        score_column_name = label + name_tag + "nn"
                        predict_labels_df[score_column_name] = y_hat

                    # --- LC
                    if "lc" in P.algorithms:
                        y_hat = train_predict_lc(df_X, df_y, df_X_test, model_config=get_model("lc"))
                        score_column_name = label + name_tag + "lc"
                        predict_labels_df[score_column_name] = y_hat

        # ===
        # futur feature set
        # ===
        if "futur" in P.feature_sets:
            features = features_futur
            name_tag = "_f_"
            train_length = P.futur_train_length

            print(f"Start training 'futur' package with {len(features)} features, name tag {name_tag}', and train length {train_length}")

            # Predict data

            df_X_test = predict_df[features]
            #df_y_test = predict_df[predict_label]  # It will be set in the loop over labels

            # Train data

            # We exclude recent objects from training, because they do not have labels yet - the labels are in future
            # In real (stream) data, we will have null labels for recent objects. During simulation, labels are available and hence we need to ignore/exclude them manually
            train_end = predict_start - label_horizon - 1
            train_start = train_end - train_length
            train_start = 0 if train_start < 0 else train_start

            train_df = df.iloc[int(train_start):int(train_end)]  # We assume that iloc is equal to index
            train_df = train_df.dropna(subset=features)

            df_X = train_df[features]
            #df_y = train_df[predict_label]  # It will be set in the loop over labels

            print(f"Train range: [{train_start}, {train_end}]={train_end-train_start}. Prediction range: [{predict_start}, {predict_end}]={predict_end-predict_start}. ")

            for label in labels:  # Train-predict different labels (and algorithms) using same X
                df_y = train_df[label]
                df_y_test = predict_df[label]

                if P.use_multiprocessing:
                    # Submit train-predict algorithms to the pool
                    execution_results = dict()
                    with ProcessPoolExecutor(max_workers=P.max_workers) as executor:
                        # --- GB
                        if "gb" in P.algorithms:
                            score_column_name = label + name_tag + "gb"
                            execution_results[score_column_name] = executor.submit(train_predict_gb, df_X, df_y, df_X_test, get_model("gb"))

                        # --- NN
                        if "nn" in P.algorithms:
                            score_column_name = label + name_tag + "nn"
                            execution_results[score_column_name] = executor.submit(train_predict_nn, df_X, df_y, df_X_test, get_model("nn"))

                        # --- LC
                        if "lc" in P.algorithms:
                            score_column_name = label + name_tag + "lc"
                            execution_results[score_column_name] = executor.submit(train_predict_lc, df_X, df_y, df_X_test, get_model("lc"))

                    # Process the results as the tasks are finished
                    for score_column_name, future in execution_results.items():
                        predict_labels_df[score_column_name] = future.result()
                        if future.exception():
                            print(f"Exception while train-predict {score_column_name}.")
                            return
                else:  # No multiprocessing - sequential execution
                    # --- GB
                    if "gb" in P.algorithms:
                        y_hat = train_predict_gb(df_X, df_y, df_X_test, model_config=get_model("gb"))
                        score_column_name = label + name_tag + "gb"
                        predict_labels_df[score_column_name] = y_hat

                    # --- NN
                    if "nn" in P.algorithms:
                        y_hat = train_predict_nn(df_X, df_y, df_X_test, model_config=get_model("nn"))
                        score_column_name = label + name_tag + "nn"
                        predict_labels_df[score_column_name] = y_hat

                    # --- LC
                    if "lc" in P.algorithms:
                        y_hat = train_predict_lc(df_X, df_y, df_X_test, model_config=get_model("lc"))
                        score_column_name = label + name_tag + "lc"
                        predict_labels_df[score_column_name] = y_hat

        #
        # Append predicted *rows* to the end of previous predicted rows
        #
        # Predictions for all labels and histories (and algorithms) have been generated for the iteration
        labels_hat_df = labels_hat_df.append(predict_labels_df)

        print(f"End step {step}/{steps}.")
        print(f"Predicted {len(predict_labels_df.columns)} labels.")


    # End of loop over prediction steps
    print("")
    print(f"Finished all {steps} prediction steps each with {stride} predicted rows (stride). ")
    print(f"Size of predicted dataframe {len(labels_hat_df)}. Number of rows in all steps {steps*stride} (steps * stride). ")
    print(f"Number of predicted columns {len(labels_hat_df.columns)}")

    #
    # Store data
    #
    out_file_name = f"{symbol}-{P.predict_file_suffix}.csv"
    out_path = data_path / out_file_name

    # We do not store features. Only selected original data, labels, and their predictions
    out_df = labels_hat_df.join(df[out_columns + labels])

    print(f"Storing output file...")
    out_df.to_csv(out_path, index=False)
    #out_df.to_parquet(out_path.with_suffix('.parquet'), engine='auto', compression=None, index=None, partition_cols=None)
    print(f"Predictions stored in file: {out_path}. Length: {len(out_df)}. Columns: {len(out_df.columns)}")

    #
    # Compute accuracy for the whole data set (all segments)
    #

    # Alternatively, generate all score column names: score_column_name = label + "_k_" + history_name
    score_lines = []
    for score_column_name in labels_hat_df.columns:
        label_column = score_column_name[0:-5]

        # Drop nans from scores
        df_scores = pd.DataFrame({"y_true": out_df[label_column], "y_predicted": out_df[score_column_name]})
        df_scores = df_scores.dropna()

        y_true = df_scores["y_true"].astype(int)
        y_predicted = df_scores["y_predicted"]
        y_predicted_class = np.where(y_predicted.values > 0.5, 1, 0)

        print(f"Using {len(df_scores)} non-nan rows for scoring.")

        score = compute_scores(y_true, y_predicted)

        score_lines.append(f"{score_column_name}, {score.get('auc'):.3f}, {score.get('ap'):.3f}, {score.get('f1'):.3f}, {score.get('precision'):.3f}, {score.get('recall'):.3f}")

    #
    # Store hyper-parameters and scores
    #
    with open(out_path.with_suffix('.txt'), "a+") as f:
        f.write("\n".join([str(x) for x in score_lines]) + "\n")

    elapsed = datetime.now() - start_dt
    print(f"Finished feature prediction in {int(elapsed.total_seconds())} seconds.")


if __name__ == '__main__':
    main()
