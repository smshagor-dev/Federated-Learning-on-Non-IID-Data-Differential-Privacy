from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QTextEdit, QVBoxLayout, QWidget

from desktop.widgets.metric_card import MetricCard
from desktop.widgets.research_card import ResearchCard
from desktop.widgets.section_header import SectionHeader


class AlgorithmsPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(
            SectionHeader(
                "Algorithms",
                "Supported training algorithms in the active desktop runtime and related implementations elsewhere in the repository.",
            )
        )
        grid = QGridLayout()
        grid.setSpacing(10)
        self.active_card = MetricCard("Active Selection", "--", "Current root runtime algorithm")
        self.supported_card = MetricCard("Root Runtime Set", "--", "Algorithms supported by python main.py")
        self.aux_card = MetricCard("Auxiliary Implementations", "--", "Additional algorithms outside the root runtime path")
        self.family_card = MetricCard("Algorithm Family", "--", "Quick reading of the active optimizer style")
        grid.addWidget(self.active_card, 0, 0)
        grid.addWidget(self.supported_card, 0, 1)
        grid.addWidget(self.aux_card, 0, 2)
        grid.addWidget(self.family_card, 0, 3)
        layout.addLayout(grid)

        body = QGridLayout()
        body.setSpacing(10)
        self.notes_card = ResearchCard()
        self.notes_card.layout.addWidget(
            SectionHeader(
                "Algorithm Notes",
                "Behavioral context, scope boundaries, and implementation coverage.",
            )
        )
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.notes_card.layout.addWidget(self.text, 1)
        body.addWidget(self.notes_card, 0, 0)

        self.selection_card = ResearchCard()
        self.selection_card.layout.addWidget(
            SectionHeader(
                "Selection Guide",
                "When to choose the current algorithm relative to the other available runtime options.",
            )
        )
        self.selection_text = QTextEdit()
        self.selection_text.setReadOnly(True)
        self.selection_card.layout.addWidget(self.selection_text, 1)
        body.addWidget(self.selection_card, 0, 1)
        layout.addLayout(body, 1)

    def update_view(self, algorithms: tuple[str, ...], config: dict) -> None:
        active = config["algorithm"]["name"]
        family = self._family(active)
        self.active_card.set_value(active.upper())
        self.supported_card.set_value(str(len(algorithms)))
        self.supported_card.set_caption(", ".join(item.upper() for item in algorithms))
        self.aux_card.set_value("FedSAM, Ditto, Per-FedAvg, C++ FedOpt")
        self.aux_card.set_caption("Additional subsystem implementations available outside the root launcher")
        self.family_card.set_value(family)
        self.family_card.set_caption("Derived from the active runtime selection")
        lines = [
            f"Active root runtime selection: {active}",
            "",
            "Algorithms available in the active root desktop simulator:",
        ]
        for algorithm in algorithms:
            lines.append(f"- {algorithm}")
        lines.extend(
            [
                "",
                "Additional subsystem implementations present elsewhere in the repository:",
                "- fedsam",
                "- ditto",
                "- per_fedavg",
                "- C++ FedAdagrad / FedAdam / FedYogi",
            ]
        )
        self.text.setPlainText("\n".join(lines))
        self.selection_text.setPlainText(
            "\n".join(
                [
                    f"{active} is currently active.",
                    f"Algorithm family: {family}.",
                    "Use FedAvg for the cleanest baseline and simplest aggregation behavior.",
                    "Use FedProx when client drift or strong non-IID heterogeneity destabilizes local training.",
                    "Use SCAFFOLD when control variates are needed to reduce client-drift bias across rounds.",
                ]
            )
        )

    @staticmethod
    def _family(name: str) -> str:
        lookup = {
            "fedavg": "Baseline Averaging",
            "fedprox": "Proximal Stabilization",
            "scaffold": "Control Variates",
            "fedadam": "Adaptive Server Optimizer",
            "fedyogi": "Adaptive Server Optimizer",
            "fedadagrad": "Adaptive Server Optimizer",
        }
        return lookup.get(name.lower(), "Research Variant")
