"""Diálogos del resultado de la verificación de fidelidad al guardar."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox, QWidget

from pdf_studio.core.documento import Guardado


def preguntar_guardar_igual(parent: QWidget, guardado: Guardado) -> bool:
    """Se llama cuando la verificación falló. Devuelve True si el usuario quiere guardar igual."""
    detalle = []
    for d in guardado.fidelidad.diferencias:
        detalle.append(f"Página {d.pagina + 1}: {d.motivo} ({d.pixeles_distintos} píxeles, "
                       f"{d.porcentaje:.3f}%)")
    caja = QMessageBox(parent)
    caja.setIcon(QMessageBox.Icon.Warning)
    caja.setWindowTitle("Verificación de fidelidad")
    caja.setText("El resultado no es idéntico al original en páginas que no editaste.")
    caja.setInformativeText(guardado.resumen() + "\n\n¿Querés guardarlo de todos modos?")
    if detalle:
        caja.setDetailedText("\n".join(detalle))
    si = caja.addButton("Guardar igual", QMessageBox.ButtonRole.AcceptRole)
    no = caja.addButton("No guardar", QMessageBox.ButtonRole.RejectRole)
    caja.setDefaultButton(no)
    caja.exec()
    return caja.clickedButton() is si


def mostrar_guardado(parent: QWidget, guardado: Guardado, destino: Path) -> None:
    caja = QMessageBox(parent)
    caja.setIcon(QMessageBox.Icon.Information if guardado.es_fiel else QMessageBox.Icon.Warning)
    caja.setWindowTitle("Guardado")
    caja.setText(f"Se guardó: {destino.name}")
    caja.setInformativeText(f"{destino.parent}\n\n{guardado.resumen()}")
    caja.exec()
