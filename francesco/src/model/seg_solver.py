import tensorflow as tf
import copy

from tf_utils import Tensorboard, MetricsManager, misc


class Solver:

    def __init__(self, ckp_path, params, modes):
        self.ckp_path = ckp_path
        self.params = params
        if ckp_path is not None:
            misc.save_json(ckp_path + "params.json", params)

        # Best metrics and Tensorboard scalars
        self.best_metrics, self.tb_manager = self.init_metrics(modes)
        self.consecutive_check_validation = 0

        # Optimization
        self.loss_manager = MetricsManager()
        self.optimizer = tf.keras.optimizers.Adam(learning_rate=params["lr"])

        # Gauss kernel
        self.window = misc.gauss_3D()

    def init_metrics(self, modes):
        """
        Initialize best metrics and Tensorboard ones
        """
        metrics = {"loss": {"value": 100000., "type": "Mean"}, "dice_score_by_class": []}

        for class_index in range(self.params["out_ch"]):
            metrics["dice_score_by_class"].append({"value": 0., "type": "Mean"})

        best_metrics = {}
        for mode in modes:
            best_metrics[mode] = copy.deepcopy(metrics)

        return best_metrics, Tensorboard(self.ckp_path, metrics, modes)

    def iterate_dataset(self, network, dataset, mode, n_epoch):

        iterations = 0
        for batch in dataset:
            iterations += 1
            # One-hot encoding
            y = tf.keras.utils.to_categorical(tf.cast(batch["y"], tf.int32), self.params["out_ch"])

            if "train" in mode:
                predictions, metrics = self.train_step(network, batch["x"], y)
            else:
                predictions, metrics = self.test_step(network, batch["x"], y)
            self.tb_manager.update_metrics(metrics)

            if iterations >= 500:
                break

        epoch_metrics = self.tb_manager.get_current_metrics()
        self.tb_manager.write_summary(mode, n_epoch, {"x": batch["x"], "y": tf.expand_dims(batch["y"], -1), "pred": predictions})

        if "train" not in mode:
            network.save_weights(self.ckp_path + "epoch-" + str(n_epoch))

        return epoch_metrics

    @tf.function
    def train_step(self, network, x, y):

        with tf.GradientTape() as tape:
            logits = network(x, training=True)
            dice_scores_by_class = self.loss_manager.dice_score_from_logits(y, logits)
            loss = self.loss_manager.metrics[self.params["loss"]](y, logits)

        gradients = tape.gradient(loss, network.trainable_variables)
        self.optimizer.apply_gradients(zip(gradients, network.trainable_variables))

        return misc.get_argmax_prediction(logits), {"loss": loss, "dice_score_by_class": dice_scores_by_class}

    @tf.function
    def test_step(self, network, x, y):
        logits = network(x, training=False)

        if y is None:
            return misc.get_argmax_prediction(logits)
        else:
            dice_scores_by_class = self.loss_manager.dice_score_from_logits(y, logits)
            loss = self.loss_manager.metrics[self.params["loss"]](y, logits)
            return misc.get_argmax_prediction(logits), {"loss": loss, "dice_score_by_class": dice_scores_by_class}

    def save_model(self, network, metrics, mode):

        for key in metrics:
            if isinstance(metrics[key], list):
                for i in range(len(metrics[key])):
                    is_better = metrics[key][i] < self.best_metrics[mode][key][i]["value"] if "loss" in key else metrics[key][i] > self.best_metrics[mode][key][i]["value"]
                    if is_better:
                        self.consecutive_check_validation = 0
                        self.best_metrics[mode][key][i]["value"] = metrics[key][i]
                        network.save_weights(self.ckp_path + mode + "-" + key + "-" + str(i))
            else:
                is_better = metrics[key] < self.best_metrics[mode][key]["value"] if "loss" in key else metrics[key] > self.best_metrics[mode][key]["value"]
                if is_better:
                    self.consecutive_check_validation = 0
                    self.best_metrics[mode][key]["value"] = metrics[key]
                    network.save_weights(self.ckp_path + mode + "-" + key)

        self.consecutive_check_validation += 1
