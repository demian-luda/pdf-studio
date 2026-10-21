"""Modelo de un documento reconstruido a partir de un escaneo.

Lo arma `core.conversion` (OCR + análisis de la imagen) y `core.maquetado` (estructura),
lo revisa el usuario en la pantalla de revisión y lo exporta `core.exportar_editable`
(.docx y PDF con texto real). Todas las medidas van en puntos (1/72 de pulgada), en las
coordenadas de la página: x hacia la derecha, y hacia abajo, origen arriba a la izquierda.

Es serializable (a_dict / desde_dict) porque el análisis corre en otro proceso.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum

Caja = tuple[float, float, float, float]

# Una palabra con menos confianza que esto se marca como dudosa en la revisión
UMBRAL_DUDOSA = 80.0


class Tipo(str, Enum):
    PARRAFO = "parrafo"
    TITULO = "titulo"
    ITEM = "item"            # elemento de una lista (con viñeta o numerado)
    TABLA = "tabla"
    IMAGEN = "imagen"


class Alineacion(str, Enum):
    IZQUIERDA = "izquierda"
    CENTRO = "centro"
    DERECHA = "derecha"
    JUSTIFICADO = "justificado"


def union(cajas) -> Caja:
    cajas = list(cajas)
    if not cajas:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(c[0] for c in cajas), min(c[1] for c in cajas), max(c[2] for c in cajas), max(c[3] for c in cajas))


@dataclass
class Palabra:
    id: str
    texto: str
    caja: Caja
    conf: float                     # confianza del OCR, 0 a 100
    negrita: bool = False
    color: str = "000000"
    revisada: bool = False          # el usuario la confirmó o la corrigió
    trazo: float = 0.0              # grosor medio del trazo en pt (para detectar negritas)

    @property
    def dudosa(self) -> bool:
        return self.conf < UMBRAL_DUDOSA and not self.revisada

    @classmethod
    def desde_dict(cls, d: dict) -> "Palabra":
        return cls(d["id"], d["texto"], tuple(d["caja"]), float(d["conf"]), bool(d.get("negrita", False)),
                   d.get("color", "000000"), bool(d.get("revisada", False)), float(d.get("trazo", 0.0)))


@dataclass
class Linea:
    palabras: list[Palabra]
    tamano: float                   # tamaño de letra estimado (pt)
    base: float = 0.0               # y de la línea de base

    @property
    def caja(self) -> Caja:
        return union(p.caja for p in self.palabras)

    @property
    def texto(self) -> str:
        return " ".join(p.texto for p in self.palabras)

    @property
    def negrita(self) -> bool:
        letras = sum(len(p.texto) for p in self.palabras)
        return letras > 0 and sum(len(p.texto) for p in self.palabras if p.negrita) >= 0.7 * letras

    @classmethod
    def desde_dict(cls, d: dict) -> "Linea":
        return cls([Palabra.desde_dict(p) for p in d["palabras"]], float(d["tamano"]), float(d.get("base", 0.0)))


@dataclass
class Celda:
    fila: int
    columna: int
    filas: int = 1                  # celdas combinadas
    columnas: int = 1
    bloques: list["Bloque"] = field(default_factory=list)

    @classmethod
    def desde_dict(cls, d: dict) -> "Celda":
        return cls(int(d["fila"]), int(d["columna"]), int(d.get("filas", 1)), int(d.get("columnas", 1)),
                   [Bloque.desde_dict(b) for b in d.get("bloques", [])])


@dataclass
class Bloque:
    tipo: Tipo
    lineas: list[Linea] = field(default_factory=list)
    nivel: int = 0                  # título: 1 a 3; ítem: nivel de sangría (0 a 2)
    numerado: bool = False
    marcador: str = ""              # como aparece en el escaneo: "•", "1.", "a)"
    numero: int = 1                 # valor del primer ítem numerado
    x_marcador: float = 0.0
    alineacion: Alineacion = Alineacion.IZQUIERDA
    sangria: float = 0.0            # desde el margen izquierdo (pt)
    sangria_primera: float = 0.0    # de la primera línea respecto de las demás (negativa = francesa)
    espacio_antes: float = 0.0
    interlineado: float = 0.0       # distancia entre líneas de base (0 = la normal)
    salto_antes: bool = False       # empieza una página nueva
    caja_fija: Caja | None = None   # tablas e imágenes
    # Tabla
    filas: list[float] = field(default_factory=list)       # y de las líneas horizontales (n + 1)
    columnas: list[float] = field(default_factory=list)    # x de las líneas verticales (m + 1)
    celdas: list[Celda] = field(default_factory=list)
    # Imagen
    archivo: str = ""
    clase: str = ""                 # "logo", "firma", "sello", "imagen"

    @property
    def caja(self) -> Caja:
        if self.caja_fija is not None:
            return self.caja_fija
        cajas = [l.caja for l in self.lineas]
        if self.marcador and self.lineas:
            primera = self.lineas[0].caja
            cajas.append((self.x_marcador, primera[1], self.x_marcador + 1, primera[3]))
        return union(cajas)

    @property
    def palabras(self) -> list[Palabra]:
        palabras = [p for l in self.lineas for p in l.palabras]
        for c in self.celdas:
            for b in c.bloques:
                palabras += b.palabras
        return palabras

    @property
    def tamano(self) -> float:
        if not self.lineas:
            return 0.0
        pesos = [(l.tamano, sum(len(p.texto) for p in l.palabras)) for l in self.lineas]
        total = sum(w for _, w in pesos) or 1
        return round(sum(t * w for t, w in pesos) / total * 2) / 2

    @property
    def negrita(self) -> bool:
        return bool(self.lineas) and all(l.negrita for l in self.lineas)

    def a_dict(self) -> dict:
        d = asdict(self)
        d["tipo"], d["alineacion"] = self.tipo.value, self.alineacion.value
        return d

    @classmethod
    def desde_dict(cls, d: dict) -> "Bloque":
        return cls(Tipo(d["tipo"]), [Linea.desde_dict(l) for l in d.get("lineas", [])], int(d.get("nivel", 0)),
                   bool(d.get("numerado", False)), d.get("marcador", ""), int(d.get("numero", 1)),
                   float(d.get("x_marcador", 0.0)), Alineacion(d.get("alineacion", "izquierda")),
                   float(d.get("sangria", 0.0)), float(d.get("sangria_primera", 0.0)),
                   float(d.get("espacio_antes", 0.0)), float(d.get("interlineado", 0.0)),
                   bool(d.get("salto_antes", False)),
                   tuple(d["caja_fija"]) if d.get("caja_fija") else None,
                   [float(v) for v in d.get("filas", [])], [float(v) for v in d.get("columnas", [])],
                   [Celda.desde_dict(c) for c in d.get("celdas", [])], d.get("archivo", ""), d.get("clase", ""))


@dataclass
class PaginaReconstruida:
    numero: int                     # 0 = primera
    ancho: float
    alto: float
    imagen: str = ""                # el escaneo enderezado (PNG), para la revisión
    bloques: list[Bloque] = field(default_factory=list)
    encabezado: list[Bloque] = field(default_factory=list)
    pie: list[Bloque] = field(default_factory=list)

    def todos(self) -> list[Bloque]:
        return self.encabezado + self.bloques + self.pie

    @classmethod
    def desde_dict(cls, d: dict) -> "PaginaReconstruida":
        return cls(int(d["numero"]), float(d["ancho"]), float(d["alto"]), d.get("imagen", ""),
                   [Bloque.desde_dict(b) for b in d.get("bloques", [])],
                   [Bloque.desde_dict(b) for b in d.get("encabezado", [])],
                   [Bloque.desde_dict(b) for b in d.get("pie", [])])


@dataclass
class Fuente:
    familia: str                    # p. ej. "Arial"
    clase: str                      # sans / serif / mono
    regular: str                    # archivos .ttf/.ttc
    negrita: str = ""
    indice_regular: int = 0
    indice_negrita: int = 0
    origen: str = "sistema"         # "sistema" (instalada en Windows) o "incluida" (viene con PDF Studio)

    @classmethod
    def desde_dict(cls, d: dict) -> "Fuente":
        return cls(d["familia"], d["clase"], d["regular"], d.get("negrita", ""), int(d.get("indice_regular", 0)),
                   int(d.get("indice_negrita", 0)), d.get("origen", "sistema"))


@dataclass
class Reconstruccion:
    paginas: list[PaginaReconstruida]
    fuente: Fuente | None = None
    alternativas: list[Fuente] = field(default_factory=list)   # otras de la misma clase, de más a menos parecida
    margenes: Caja = (72.0, 72.0, 72.0, 72.0)                  # izquierdo, superior, derecho, inferior
    distancia_encabezado: float = 36.0
    distancia_pie: float = 36.0
    tamano_cuerpo: float = 11.0
    interlineado_cuerpo: float = 0.0
    avisos: list[str] = field(default_factory=list)

    def palabras(self) -> list[Palabra]:
        return [p for pag in self.paginas for b in pag.todos() for p in b.palabras]

    def dudosas(self) -> list[Palabra]:
        return [p for p in self.palabras() if p.dudosa]

    def buscar(self, id_palabra: str) -> Palabra | None:
        return next((p for p in self.palabras() if p.id == id_palabra), None)

    def pagina_de(self, id_palabra: str) -> int:
        return int(id_palabra.split("-")[0][1:]) if id_palabra.startswith("p") else 0

    def a_dict(self) -> dict:
        return {"paginas": [{"numero": p.numero, "ancho": p.ancho, "alto": p.alto, "imagen": p.imagen,
                             "bloques": [b.a_dict() for b in p.bloques],
                             "encabezado": [b.a_dict() for b in p.encabezado],
                             "pie": [b.a_dict() for b in p.pie]} for p in self.paginas],
                "fuente": asdict(self.fuente) if self.fuente else None,
                "alternativas": [asdict(f) for f in self.alternativas],
                "margenes": list(self.margenes), "distancia_encabezado": self.distancia_encabezado,
                "distancia_pie": self.distancia_pie, "tamano_cuerpo": self.tamano_cuerpo,
                "interlineado_cuerpo": self.interlineado_cuerpo, "avisos": list(self.avisos)}

    @classmethod
    def desde_dict(cls, d: dict) -> "Reconstruccion":
        return cls([PaginaReconstruida.desde_dict(p) for p in d["paginas"]],
                   Fuente.desde_dict(d["fuente"]) if d.get("fuente") else None,
                   [Fuente.desde_dict(f) for f in d.get("alternativas", [])],
                   tuple(d.get("margenes", (72.0, 72.0, 72.0, 72.0))), float(d.get("distancia_encabezado", 36.0)),
                   float(d.get("distancia_pie", 36.0)), float(d.get("tamano_cuerpo", 11.0)),
                   float(d.get("interlineado_cuerpo", 0.0)), list(d.get("avisos", [])))
