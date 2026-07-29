from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from desktop.widgets.configuration_group import ConfigurationGroup
from desktop.widgets.research_card import ResearchCard
from desktop.widgets.section_header import SectionHeader


class ExperimentPage(QWidget):
    run_requested = Signal(dict)
    stop_requested = Signal()
    browse_config_requested = Signal()
    reload_requested = Signal()

    def __init__(self, algorithms: list[str], parent=None) -> None:
        super().__init__(parent)
        self._loaded_config: dict | None = None
        self._loaded_config_path = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        root.addWidget(
            SectionHeader(
                "New Experiment",
                "Scrollable configuration workspace inspired directly by the original experiment-control panel.",
            )
        )

        shell = QScrollArea()
        shell.setWidgetResizable(True)
        shell.setFrameShape(QFrame.NoFrame)
        shell.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        shell.setWidget(inner)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        root.addWidget(shell, 1)

        config_card = ResearchCard()
        config_row = QHBoxLayout()
        self.config_path_edit = QLineEdit()
        browse_button = QPushButton("Browse")
        reload_button = QPushButton("Reload")
        browse_button.clicked.connect(self.browse_config_requested.emit)
        reload_button.clicked.connect(self.reload_requested.emit)
        config_row.addWidget(self.config_path_edit, 1)
        config_row.addWidget(browse_button)
        config_row.addWidget(reload_button)
        config_card.layout.addWidget(
            SectionHeader(
                "Configuration Source",
                "Load and review the YAML configuration used as the base experiment specification.",
            )
        )
        config_card.layout.addLayout(config_row)
        layout.addWidget(config_card)

        self.results_dir_edit = QLineEdit()
        self.dataset_combo = QComboBox()
        self.dataset_combo.addItems(["CIFAR10", "MNIST"])
        self.partition_combo = QComboBox()
        self.partition_combo.addItems(["dirichlet", "pathological"])
        self.algorithm_combo = QComboBox()
        self.algorithm_combo.addItems([*algorithms, "all"])
        self.device_combo = QComboBox()
        self.device_combo.addItems(["auto", "cpu", "cuda"])

        self.seed_spin = self._int_spin(0, 10_000_000)
        self.num_clients_spin = self._int_spin(1, 10_000)
        self.rounds_spin = self._int_spin(1, 100_000)
        self.local_epochs_spin = self._int_spin(1, 1_000)
        self.batch_size_spin = self._int_spin(1, 8_192)
        self.eval_batch_size_spin = self._int_spin(1, 8_192)
        self.classes_per_client_spin = self._int_spin(1, 100)
        self.min_partition_size_spin = self._int_spin(1, 100_000)

        self.alpha_spin = self._double_spin(0.001, 100.0, 3)
        self.sample_rate_spin = self._double_spin(0.0, 1.0, 3)
        self.grad_clip_norm_spin = self._double_spin(0.0, 1000.0, 4)
        self.server_lr_spin = self._double_spin(0.0001, 100.0, 4)
        self.optimizer_lr_spin = self._double_spin(0.000001, 10.0, 5)
        self.momentum_spin = self._double_spin(0.0, 1.0, 3)
        self.weight_decay_spin = self._double_spin(0.0, 1.0, 6)
        self.mu_spin = self._double_spin(0.0, 100.0, 4)
        self.update_clip_norm_spin = self._double_spin(0.0001, 1000.0, 4)
        self.noise_spin = self._double_spin(0.0, 1000.0, 4)
        self.delta_spin = self._double_spin(0.0, 1.0, 8)
        self.dp_checkbox = QCheckBox("Enable differential privacy")
        self.sampling_strategy_combo = QComboBox()
        self.sampling_strategy_combo.addItems(["poisson", "fixed_without_replacement"])
        self.aggregation_weighting_combo = QComboBox()
        self.aggregation_weighting_combo.addItems(["uniform", "sample_count"])

        grid = QGridLayout()
        grid.setSpacing(10)
        layout.addLayout(grid)

        general = ConfigurationGroup("General", "Global runtime settings and output location.")
        general.add_row("Results directory", self.results_dir_edit)
        general.add_row("Device", self.device_combo)
        general.add_row("Seed", self.seed_spin)

        dataset = ConfigurationGroup("Dataset", "Dataset and non-IID partition selection.")
        dataset.add_row("Dataset", self.dataset_combo)
        dataset.add_row("Partition", self.partition_combo)
        dataset.add_row("Dirichlet alpha", self.alpha_spin)
        dataset.add_row("Classes per client", self.classes_per_client_spin)
        dataset.add_row("Minimum partition size", self.min_partition_size_spin)

        federated = ConfigurationGroup("Federated Training", "Communication-round controls and sampling configuration.")
        federated.add_row("Number of clients", self.num_clients_spin)
        federated.add_row("Sampling rate", self.sample_rate_spin)
        federated.add_row("Sampling strategy", self.sampling_strategy_combo)
        federated.add_row("Aggregation weighting", self.aggregation_weighting_combo)
        federated.add_row("Communication rounds", self.rounds_spin)
        federated.add_row("Local epochs", self.local_epochs_spin)
        federated.add_row("Batch size", self.batch_size_spin)
        federated.add_row("Server learning rate", self.server_lr_spin)

        algorithm_group = ConfigurationGroup("Algorithm", "Local optimizer and aggregation-specific coefficients.")
        algorithm_group.add_row("Algorithm", self.algorithm_combo)
        algorithm_group.add_row("Optimizer learning rate", self.optimizer_lr_spin)
        algorithm_group.add_row("Momentum", self.momentum_spin)
        algorithm_group.add_row("Weight decay", self.weight_decay_spin)
        algorithm_group.add_row("Grad clip norm", self.grad_clip_norm_spin)
        algorithm_group.add_row("FedProx mu", self.mu_spin)

        privacy = ConfigurationGroup("Differential Privacy", "Trusted-server central DP controls for clipped client updates.")
        privacy.add_row("DP enabled", self.dp_checkbox)
        privacy.add_row("Update clip norm", self.update_clip_norm_spin)
        privacy.add_row("Noise multiplier", self.noise_spin)
        privacy.add_row("Target delta", self.delta_spin)

        evaluation = ConfigurationGroup("Evaluation", "Held-out evaluation batching.")
        evaluation.add_row("Eval batch size", self.eval_batch_size_spin)

        grid.addWidget(general, 0, 0)
        grid.addWidget(dataset, 0, 1)
        grid.addWidget(federated, 1, 0)
        grid.addWidget(algorithm_group, 1, 1)
        grid.addWidget(privacy, 2, 0)
        grid.addWidget(evaluation, 2, 1)

        lower = QHBoxLayout()
        lower.setSpacing(10)
        self.preview_card = ResearchCard()
        self.preview_card.layout.addWidget(
            SectionHeader(
                "Run Preview",
                "Editable runtime summary derived from the current form values before execution.",
            )
        )
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_card.layout.addWidget(self.preview_text, 1)
        lower.addWidget(self.preview_card, 3)

        self.validation_card = ResearchCard()
        self.validation_card.layout.addWidget(
            SectionHeader(
                "Validation Notes",
                "Quick checks for common configuration mistakes before launching an experiment.",
            )
        )
        self.validation_text = QTextEdit()
        self.validation_text.setReadOnly(True)
        self.validation_card.layout.addWidget(self.validation_text, 1)
        lower.addWidget(self.validation_card, 2)
        layout.addLayout(lower)

        action_card = ResearchCard()
        action_card.layout.addWidget(
            SectionHeader(
                "Run Actions",
                "Start, stop, preview, and manage the experiment lifecycle from the desktop workspace.",
            )
        )
        actions = QHBoxLayout()
        self.run_button = QPushButton("Start Experiment")
        self.run_button.setProperty("primary", True)
        self.stop_button = QPushButton("Stop Run")
        self.load_button = QPushButton("Load Configuration")
        self.load_button.clicked.connect(self.browse_config_requested.emit)
        self.reload_button = QPushButton("Save / Reload")
        self.reload_button.clicked.connect(self.reload_requested.emit)
        self.reset_button = QPushButton("Reset Form")
        self.preview_button = QPushButton("Refresh Preview")
        self.run_button.clicked.connect(lambda: self.run_requested.emit(self.collect_updates()))
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.reset_button.clicked.connect(self._reset_form)
        self.preview_button.clicked.connect(self._refresh_preview)
        for button in [self.load_button, self.reload_button, self.reset_button, self.preview_button, self.run_button, self.stop_button]:
            actions.addWidget(button)
        actions.addStretch(1)
        action_card.layout.addLayout(actions)
        layout.addWidget(action_card)
        layout.addStretch(1)

        for widget in [
            self.results_dir_edit,
            self.dataset_combo,
            self.partition_combo,
            self.algorithm_combo,
            self.device_combo,
            self.sampling_strategy_combo,
            self.aggregation_weighting_combo,
            self.dp_checkbox,
        ]:
            if hasattr(widget, "currentTextChanged"):
                widget.currentTextChanged.connect(self._refresh_preview)  # type: ignore[attr-defined]
            elif hasattr(widget, "stateChanged"):
                widget.stateChanged.connect(self._refresh_preview)  # type: ignore[attr-defined]
        for widget in [
            self.seed_spin,
            self.num_clients_spin,
            self.rounds_spin,
            self.local_epochs_spin,
            self.batch_size_spin,
            self.eval_batch_size_spin,
            self.classes_per_client_spin,
            self.min_partition_size_spin,
            self.alpha_spin,
            self.sample_rate_spin,
            self.server_lr_spin,
            self.optimizer_lr_spin,
            self.momentum_spin,
            self.weight_decay_spin,
            self.grad_clip_norm_spin,
            self.mu_spin,
            self.update_clip_norm_spin,
            self.noise_spin,
            self.delta_spin,
        ]:
            widget.valueChanged.connect(self._refresh_preview)
        self.results_dir_edit.textChanged.connect(self._refresh_preview)

    def _int_spin(self, minimum: int, maximum: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        return widget

    def _double_spin(self, minimum: float, maximum: float, decimals: int) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setDecimals(decimals)
        widget.setSingleStep(0.01)
        return widget

    def load_config(self, config: dict, config_path: str) -> None:
        self._loaded_config = deepcopy(config)
        self._loaded_config_path = config_path
        self.config_path_edit.setText(config_path)
        self.results_dir_edit.setText(str(config["system"]["results_dir"]))
        self.device_combo.setCurrentText(str(config["system"]["device"]))
        self.seed_spin.setValue(int(config["system"]["seed"]))
        self.dataset_combo.setCurrentText(str(config["data"]["dataset"]))
        self.partition_combo.setCurrentText(str(config["data"]["partition"]))
        self.alpha_spin.setValue(float(config["data"]["alpha"]))
        self.classes_per_client_spin.setValue(int(config["data"]["classes_per_client"]))
        self.min_partition_size_spin.setValue(int(config["data"]["min_partition_size"]))
        self.algorithm_combo.setCurrentText(str(config["algorithm"]["name"]))
        self.num_clients_spin.setValue(int(config["federated"]["num_clients"]))
        self.sample_rate_spin.setValue(float(config["federated"]["sample_rate"]))
        self.sampling_strategy_combo.setCurrentText(str(config["federated"]["sampling_strategy"]))
        self.aggregation_weighting_combo.setCurrentText(str(config["federated"]["aggregation_weighting"]))
        self.rounds_spin.setValue(int(config["federated"]["rounds"]))
        self.local_epochs_spin.setValue(int(config["federated"]["local_epochs"]))
        self.batch_size_spin.setValue(int(config["federated"]["batch_size"]))
        self.eval_batch_size_spin.setValue(int(config["evaluation"]["eval_batch_size"]))
        self.server_lr_spin.setValue(float(config["federated"]["server_lr"]))
        self.optimizer_lr_spin.setValue(float(config["optimizer"]["lr"]))
        self.momentum_spin.setValue(float(config["optimizer"]["momentum"]))
        self.weight_decay_spin.setValue(float(config["optimizer"]["weight_decay"]))
        self.grad_clip_norm_spin.setValue(float(config["optimizer"]["grad_clip_norm"] or 0.0))
        self.mu_spin.setValue(float(config["algorithm"]["mu"]))
        self.dp_checkbox.setChecked(bool(config["dp"]["enabled"]))
        self.update_clip_norm_spin.setValue(float(config["dp"]["update_clip_norm"]))
        self.noise_spin.setValue(float(config["dp"]["noise_multiplier"]))
        self.delta_spin.setValue(float(config["dp"]["target_delta"]))
        self._refresh_preview()

    def collect_updates(self) -> dict:
        return {
            "system": {
                "results_dir": self.results_dir_edit.text().strip() or "results",
                "device": self.device_combo.currentText(),
                "seed": self.seed_spin.value(),
            },
            "data": {
                "dataset": self.dataset_combo.currentText(),
                "partition": self.partition_combo.currentText(),
                "alpha": self.alpha_spin.value(),
                "classes_per_client": self.classes_per_client_spin.value(),
                "min_partition_size": self.min_partition_size_spin.value(),
            },
            "federated": {
                "num_clients": self.num_clients_spin.value(),
                "sample_rate": self.sample_rate_spin.value(),
                "sampling_strategy": self.sampling_strategy_combo.currentText(),
                "aggregation_weighting": self.aggregation_weighting_combo.currentText(),
                "rounds": self.rounds_spin.value(),
                "local_epochs": self.local_epochs_spin.value(),
                "batch_size": self.batch_size_spin.value(),
                "server_lr": self.server_lr_spin.value(),
            },
            "optimizer": {
                "lr": self.optimizer_lr_spin.value(),
                "momentum": self.momentum_spin.value(),
                "weight_decay": self.weight_decay_spin.value(),
                "grad_clip_norm": None if self.grad_clip_norm_spin.value() == 0.0 else self.grad_clip_norm_spin.value(),
            },
            "algorithm": {
                "name": self.algorithm_combo.currentText(),
                "mu": self.mu_spin.value(),
            },
            "dp": {
                "enabled": self.dp_checkbox.isChecked(),
                "update_clip_norm": self.update_clip_norm_spin.value(),
                "noise_multiplier": self.noise_spin.value(),
                "target_delta": self.delta_spin.value(),
            },
            "evaluation": {
                "eval_batch_size": self.eval_batch_size_spin.value(),
            },
        }

    def _refresh_preview(self, *_args) -> None:
        updates = self.collect_updates()
        self.preview_text.setPlainText(
            "\n".join(
                [
                    f"Config path: {self.config_path_edit.text().strip() or '--'}",
                    f"Results dir: {updates['system']['results_dir']}",
                    f"Dataset: {updates['data']['dataset']} | Partition: {updates['data']['partition']} | alpha: {updates['data']['alpha']:.3f}",
                    f"Algorithm: {updates['algorithm']['name']} | Device: {updates['system']['device']}",
                    f"Clients: {updates['federated']['num_clients']} | Sample rate: {updates['federated']['sample_rate']:.2f} | Strategy: {updates['federated']['sampling_strategy']}",
                    f"Weighting: {updates['federated']['aggregation_weighting']} | Rounds: {updates['federated']['rounds']}",
                    f"Local epochs: {updates['federated']['local_epochs']} | Batch size: {updates['federated']['batch_size']}",
                    f"DP enabled: {updates['dp']['enabled']} | noise: {updates['dp']['noise_multiplier']} | update clip: {updates['dp']['update_clip_norm']} | grad clip: {updates['optimizer']['grad_clip_norm']}",
                ]
            )
        )
        issues: list[str] = []
        if updates["federated"]["sample_rate"] < 0 or updates["federated"]["sample_rate"] > 1:
            issues.append("Sampling rate should stay within [0, 1].")
        if updates["federated"]["num_clients"] < 2:
            issues.append("At least 2 clients are recommended for federated learning behavior.")
        if updates["dp"]["enabled"] and updates["dp"]["noise_multiplier"] <= 0:
            issues.append("Differential privacy is enabled but noise multiplier is not positive.")
        if updates["dp"]["enabled"] and updates["federated"]["sampling_strategy"] != "poisson":
            issues.append("DP accounting requires Poisson client sampling in the root runtime.")
        if updates["dp"]["enabled"] and updates["federated"]["aggregation_weighting"] != "uniform":
            issues.append("DP-enabled runs require uniform client weighting in the root runtime.")
        if updates["algorithm"]["name"] in {"scaffold", "all"} and updates["federated"]["aggregation_weighting"] != "uniform":
            issues.append("SCAFFOLD and algorithm=all currently require uniform client weighting.")
        if updates["system"]["device"] == "cuda" and not updates["system"]["results_dir"]:
            issues.append("CUDA is selected, but results directory is empty.")
        self.validation_text.setPlainText("\n".join(issues) if issues else "Configuration looks internally consistent for a desktop-managed run.")

    def _reset_form(self) -> None:
        if self._loaded_config is not None:
            self.load_config(self._loaded_config, self._loaded_config_path)
