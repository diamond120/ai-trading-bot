import itertools
from pathlib import Path

from joblib import dump, load

from keras.models import Sequential, save_model, load_model

"""
Use cases:
- conventional stocks and indexes like DJ-index, DAX etc. each in its folder and daily klines - how to process
- forex with EURUSD etc.

- currently we have "_k_" in score columns which stay for feature set (probably). 
  in addition, we have "kline" used for score column in "train_features": ["kline"]
  we need to somehow conceptualize this.
  for example, train_set name which references its list
  currently feature list is in "features_kline"

- implement fine-grained label generation with two dimensions: levels/jumps (values and suffixes), tolerances (values and suffixes)
  we need fine-grained tolerance like 0.025 for small levels from 2 till 5
  - ideally we should be able to call it several times with different level-tolerance lists

TODO:
- performance metrics should be per month/transaction (not absolute): trans/m, profit/t, profit/m, %profitable, abs (profit, tans)

- implement configuration for label generation: generators, generator configs (horizons, tolerances etc.)
- feature generators. "klines" has its own config so we need to separate them. currently they are in one common config
- feature generation (preprocessing) operations "use_differences" if true then close/volume/trades are transformed to differences.
 it is similar to is_scale in training. "difference of logarithms rather than percentage"
 - there could be also other options like apply log to certain columns or outputs 
- algo config has "predict" length - where should we use it? In rolling predictions?
- feature_generation only for last N rows from the input data which are then appended to the existing matrix by overwriting the time overlap
  - we need to guarantee the validity of feature values which require some minimum data length and do not use these values 
"""

def get_model(name: str):
    """Given model name, return its JSON object"""
    return next(x for x in models if x.get("name") == name)

def load_models_from_file(file):
    """Load model store from file to memory"""
    pass


def save_model_pair(model_path, score_column_name: str, model_pair: tuple):
    """Save two models in two files with the corresponding extensions."""
    if not isinstance(model_path, Path):
        model_path = Path(model_path)
    model_path = model_path.absolute()

    model = model_pair[0]
    scaler = model_pair[1]
    # Save scaler
    scaler_file_name = (model_path / score_column_name).with_suffix(".scaler")
    dump(scaler, scaler_file_name)
    # Save prediction model
    if score_column_name.endswith("_nn"):
        model_extension = ".h5"
        model_file_name = (model_path / score_column_name).with_suffix(model_extension)
        save_model(model, model_file_name)
    else:
        model_extension = ".pickle"
        model_file_name = (model_path / score_column_name).with_suffix(model_extension)
        dump(model, model_file_name)


def load_model_pair(model_path, score_column_name: str):
    """Load a pair consisting of scaler model (possibly null) and prediction model from two files."""
    if not isinstance(model_path, Path):
        model_path = Path(model_path)
    model_path = model_path.absolute()
    # Load scaler
    scaler_file_name = (model_path / score_column_name).with_suffix(".scaler")
    scaler = load(scaler_file_name)
    # Load prediction model
    if score_column_name.endswith("_nn"):
        model_extension = ".h5"
        model_file_name = (model_path / score_column_name).with_suffix(model_extension)
        model = load_model(model_file_name)
    else:
        model_extension = ".pickle"
        model_file_name = (model_path / score_column_name).with_suffix(model_extension)
        model = load(model_file_name)

    return (model, scaler)


def load_models(model_path, labels: list, train_features: list, algorithms: list):
    """Load all model pairs for all combinations of the model parameters and return as a dict."""
    models = {}
    for predicted_label in itertools.product(labels, train_features, algorithms):
        score_column_name = predicted_label[0] + "_" + predicted_label[1][0] + "_" + predicted_label[2]
        model_pair = load_model_pair(model_path, score_column_name)
        models[score_column_name] = model_pair
    return models


models = [
    {
        "name": "nn",
        "algo": "nn",
        "params": {
            "layers": [29], # It is equal to the number of input features (different for spot and futur). Currently not used
            "learning_rate": 0.001,
            "n_epochs": 15,  # 5 for quick analysis, 20 or 30 for production
            "bs": 128,
        },
        "train": {"is_scale": True, "length": int(1.5 * 525_600), "shifts": []},
        "predict": {"length": "1w"}
    },
    {
        "name": "lc",
        "algo": "lc",
        "params": {
            "penalty": "l2",
            "C": 1.0,
            "class_weight": None,
            "solver": "sag", # liblinear, lbfgs, sag/saga (stochastic gradient descent for large datasets, should be scaled)
            "max_iter": 200,
            # "tol": 0.1,  # Tolerance for performance (check how it influences precision)
        },
        "train": {"is_scale": True, "length": int(1.5 * 525_600), "shifts": []},
        "predict": {"length": 1440}
    },
    {
        "name": "gb",
        "algo": "gb",
        "params": {
            "objective": "cross_entropy",
            "max_depth": 1,
            "learning_rate": 0.01,
            "num_boost_round": 1_500,

            "lambda_l1": 1.0,
            "lambda_l2": 1.0,
        },
        "train": {"is_scale": False, "length": int(1.5 * 525_600), "shifts": []},
        "predict": {"length": 1440}
    },

    {
        "name": "nn_long",
        "algo": "nn",
        "params": {"layers": [29], "learning_rate": 0.001, "n_epochs": 20, "bs": 128, },
        "train": {"is_scale": True, "length": int(1.5 * 525_600), "shifts": []},
        "predict": {"length": 0}
    },
    {
        "name": "nn_middle",
        "algo": "nn",
        "params": {"layers": [29], "learning_rate": 0.001, "n_epochs": 20, "bs": 128, },
        "train": {"is_scale": True, "length": int(1.0 * 525_600), "shifts": []},
        "predict": {"length": 0}
    },
    {
        "name": "nn_short",
        "algo": "nn",
        "params": {"layers": [29], "learning_rate": 0.001, "n_epochs": 20, "bs": 128, },
        "train": {"is_scale": True, "length": int(0.5 * 525_600), "shifts": []},
        "predict": {"length": 0}
    },
]
