"""Rangos de páginas escritos por el usuario: «1-3, 5, 8-fin»."""
from __future__ import annotations


def parsear_rango(texto: str, total: int) -> list[int]:
    """Convierte «1-3, 5, 8-fin» en índices desde 0, ordenados y sin repetir.
    Vacío o «todas» = todas las páginas. Lanza ValueError con un mensaje claro."""
    texto = texto.strip().lower()
    if not texto or texto in ("todas", "todo"):
        return list(range(total))
    paginas: set[int] = set()
    for parte in texto.replace(";", ",").split(","):
        parte = parte.strip()
        if not parte:
            continue
        if "-" in parte:
            ini, _, fin = (x.strip() for x in parte.partition("-"))
            a = _numero(ini or "1", total)
            b = _numero(fin or "fin", total)
            if a > b:
                raise ValueError(f"El rango «{parte}» está al revés (el inicio es mayor que el final).")
            paginas.update(range(a, b + 1))
        else:
            paginas.add(_numero(parte, total))
    if not paginas:
        raise ValueError("No se indicó ninguna página.")
    return sorted(paginas)


def _numero(texto: str, total: int) -> int:
    if texto in ("fin", "final", "última", "ultima", "u"):
        return total - 1
    try:
        n = int(texto)
    except ValueError:
        raise ValueError(f"«{texto}» no es un número de página.") from None
    if not 1 <= n <= total:
        raise ValueError(f"La página {n} no existe (el documento tiene {total}).")
    return n - 1


def describir_rango(paginas: list[int]) -> str:
    """[0, 1, 2, 4] → «1-3, 5» (para mostrar)."""
    if not paginas:
        return ""
    partes = []
    ordenadas = sorted(set(paginas))
    inicio = previo = ordenadas[0]
    for p in ordenadas[1:] + [None]:
        if p is not None and p == previo + 1:
            previo = p
            continue
        partes.append(f"{inicio + 1}" if inicio == previo else f"{inicio + 1}-{previo + 1}")
        if p is not None:
            inicio = previo = p
    return ", ".join(partes)
