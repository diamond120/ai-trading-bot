from pathlib import Path
from typing import Union

import click
import pandas as pd

from service.App import *
from common.label_generation import *
from common.label_generation_top_bot import *

"""
This script will load a feature file (or any file with close price), and add
top-bot columns according to the label parameter, by finally storing both input
data and the labels in the output file (can be the same file as input).

Note that high-low labels are generated along with features.
"""


#
# Parameters
#
class P:
    label_sets = ["top-bot"]  # Possivle values: "high-low", "top-bot"

    in_nrows = 10_000_000


@click.command()
@click.option('--config_file', '-c', type=click.Path(), default='', help='Configuration file name')
def main(config_file):
    """
    Load a file with close price (typically feature matrix),
    compute top-bottom labels, add them to the data, and store to output file.
    """
    load_config(config_file)

    freq = "1m"
    symbol = App.config["symbol"]
    data_path = Path(App.config["data_folder"])
    if not data_path.is_dir():
        print(f"Data folder does not exist: {data_path}")
        return

    start_dt = datetime.now()

    #
    # Load input data (normally feature matrix but not necessarily)
    #
    in_file_name = f"{symbol}-{freq}-features.csv"
    in_path = (data_path / in_file_name).resolve()

    print(f"Loading data from feature file {str(in_path)}...")

    in_df = pd.read_csv(in_path, parse_dates=['timestamp'], nrows=P.in_nrows)

    print(f"Finished loading {len(in_df)} records with {len(in_df.columns)} columns.")

    # Filter (for debugging)
    #df = df.iloc[-one_year:]

    #
    # Generate labels (always the same, currently based on kline data which must be therefore present)
    #
    if "high-low" in P.label_sets:
        print(f"Generating 'high-low' labels...")
        labels = []
        horizon = App.config["label_horizon"]

        # Binary labels whether max has exceeded a threshold or not
        labels += generate_labels_thresholds(in_df, horizon=horizon)

        # Numeric label which is a ratio between areas over and under the latest price
        labels += add_area_ratio(in_df, is_future=True, column_name="close", windows=[60, 120, 180, 300], suffix = "_area_future")

        print(f"Finished generating 'high-low' labels. {len(labels)} labels generated.")

    #
    # top-bot labels
    #
    if "top-bot" in P.label_sets:
        top_level_fracs = [0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12]
        bot_level_fracs = [-x for x in top_level_fracs]

        # Tolerance 0.01
        top_labels1 = ['top6_1', 'top7_1', 'top8_1', 'top9_1', 'top10_1', 'top11_1', 'top12_1']
        bot_labels1 = ['bot6_1', 'bot7_1', 'bot8_1', 'bot9_1', 'bot10_1', 'bot11_1', 'bot12_1']

        label_names = add_extremum_features(in_df, column_name='close', level_fracs=top_level_fracs, tolerance_frac=0.01, out_names=top_labels1)
        print(f"Top labels computed: {label_names}")
        label_names = add_extremum_features(in_df, column_name='close', level_fracs=bot_level_fracs, tolerance_frac=0.01, out_names=bot_labels1)
        print(f"Bottom labels computed: {label_names}")

        # Tolerance 0.02
        top_labels2 = ['top6_2', 'top7_2', 'top8_2', 'top9_2', 'top10_2', 'top11_2', 'top12_2']
        bot_labels2 = ['bot6_2', 'bot7_2', 'bot8_2', 'bot9_2', 'bot10_2', 'bot11_2', 'bot12_2']

        label_names = add_extremum_features(in_df, column_name='close', level_fracs=top_level_fracs, tolerance_frac=0.02, out_names=top_labels2)
        print(f"Top labels computed: {label_names}")
        label_names = add_extremum_features(in_df, column_name='close', level_fracs=bot_level_fracs, tolerance_frac=0.02, out_names=bot_labels2)
        print(f"Bottom labels computed: {label_names}")

        # Tolerance 0.03
        top_labels3 = ['top6_3', 'top7_3', 'top8_3', 'top9_3', 'top10_3', 'top11_3', 'top12_3']
        bot_labels3 = ['bot6_3', 'bot7_3', 'bot8_3', 'bot9_3', 'bot10_3', 'bot11_3', 'bot12_3']

        label_names = add_extremum_features(in_df, column_name='close', level_fracs=top_level_fracs, tolerance_frac=0.03, out_names=top_labels3)
        print(f"Top labels computed: {label_names}")
        label_names = add_extremum_features(in_df, column_name='close', level_fracs=bot_level_fracs, tolerance_frac=0.03, out_names=bot_labels3)
        print(f"Bottom labels computed: {label_names}")

    # Save in output file
    out_file_name = f"{symbol}-{freq}-matrix.csv"
    out_file = (data_path / out_file_name).resolve()

    print(f"Storing file with labels. {len(in_df)} records and {len(in_df.columns)} columns in output file...")

    in_df.to_csv(out_file, index=False, float_format="%.4f")

    elapsed = datetime.now() - start_dt
    print(f"Finished label generation in {int(elapsed.total_seconds())} seconds")
    print(f"Output file location: {out_file}")


if __name__ == '__main__':
    main()
