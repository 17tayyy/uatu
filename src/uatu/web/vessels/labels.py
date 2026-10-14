"""Human-readable labels for AIS codes, in Spanish."""

# Maritime Identification Digits (first three digits of a ship's MMSI) of the flags most seen in
# the area. Others are reported as unknown rather than guessed.
_FLAGS = {
    **dict.fromkeys((209, 210, 212), "Chipre"),
    **dict.fromkeys((211, 218), "Alemania"),
    **dict.fromkeys((215, 229, 248, 249, 256), "Malta"),
    **dict.fromkeys((219, 220), "Dinamarca"),
    **dict.fromkeys((224, 225), "España"),
    **dict.fromkeys((226, 227, 228), "Francia"),
    **dict.fromkeys((232, 233, 234, 235), "Reino Unido"),
    236: "Gibraltar",
    **dict.fromkeys((237, 239, 240, 241), "Grecia"),
    242: "Marruecos",
    **dict.fromkeys((244, 245, 246), "Países Bajos"),
    247: "Italia",
    **dict.fromkeys((255, 263), "Portugal"),
    **dict.fromkeys((257, 258, 259), "Noruega"),
    **dict.fromkeys((265, 266), "Suecia"),
    271: "Turquía",
    273: "Rusia",
    **dict.fromkeys((304, 305), "Antigua y Barbuda"),
    **dict.fromkeys((308, 309, 311), "Bahamas"),
    314: "Barbados",
    341: "San Cristóbal y Nieves",
    **dict.fromkeys((351, 352, 353, 354, 355, 356, 357, 370, 371, 372, 373, 374), "Panamá"),
    **dict.fromkeys((375, 376, 377), "San Vicente y las Granadinas"),
    403: "Arabia Saudí",
    **dict.fromkeys((412, 413, 414), "China"),
    422: "Irán",
    **dict.fromkeys((431, 432), "Japón"),
    461: "Omán",
    470: "Emiratos Árabes Unidos",
    477: "Hong Kong",
    **dict.fromkeys((538,), "Islas Marshall"),
    **dict.fromkeys((563, 564, 565, 566), "Singapur"),
    **dict.fromkeys((636, 637), "Liberia"),
    **dict.fromkeys((671,), "Togo"),
    **dict.fromkeys((677,), "Tanzania"),
}


def flag(mmsi: int) -> str | None:
    """Flag state from a ship's MMSI; None for unknown or non-ship MMSIs (coast stations, AtoN...)."""
    if not 200_000_000 <= mmsi <= 799_999_999:
        return None
    return _FLAGS.get(mmsi // 1_000_000)


def ship_type(code: int | None) -> str | None:
    if code is None:
        return None
    exact = {
        30: "Pesquero", 31: "Remolcador", 32: "Remolcador", 33: "Draga", 34: "Buceo", 35: "Militar",
        36: "Velero", 37: "Recreo", 50: "Práctico", 51: "Salvamento", 52: "Remolcador",
        53: "Auxiliar de puerto", 55: "Fuerzas de seguridad", 58: "Sanitario",
    }  # fmt: skip
    if code in exact:
        return exact[code]
    by_decade = {4: "Alta velocidad", 6: "Pasaje", 7: "Carga", 8: "Petrolero / tanque", 9: "Otro"}
    return by_decade.get(code // 10)
