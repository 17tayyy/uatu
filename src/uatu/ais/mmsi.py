"""What an MMSI says it is (ITU-R M.585): a ship of some flag, a craft of a parent ship, an aid to
navigation, a coast station, an emergency beacon... or nothing that can exist.

Every format but the beacons' carries the flag's Maritime Identification Digits (MID): one that no
country was allocated means a transponder set up with a made-up number, by mistake or not.
"""

# The MIDs allocated to countries and territories (ITU MARS, as listed in October 2026).
ALLOCATED_MIDS = frozenset(
    [*range(201, 217), 218, 219, 220, *range(224, 260), *range(261, 280),
     301, *range(303, 313), 314, 316, 319, 321, 323, 325, 327, 329, 330, 331, 332, 334, 336, 338, 339,
     341, 343, 345, 347, 348, *range(350, 360), 361, 362, 364, *range(366, 380),
     401, 403, 405, 408, 410, 412, 413, 414, 416, 417, 419, 422, 423, 425, 428, 431, 432, 434, 436, 437,
     438, 440, 441, 443, 445, 447, 450, 451, 453, 455, 457, 459, 461, 463, 466, 468, 470, 471, 472, 473,
     475, 477, 478,
     501, 503, 506, 508, 510, 511, 512, 514, 515, 516, 518, 520, 523, 525, 529, 531, 533, 536, 538, 540,
     542, 544, 546, 548, 550, 553, 555, 557, 559, 561, 563, 564, 565, 566, 567, 570, 572, 574, 576, 577,
     578,
     601, 603, 605, 607, 608, 609, 610, 611, 612, 613, *range(615, 623), *range(624, 628),
     *range(629, 639), 642, 644, 645, 647, 649, 650, *range(654, 658), *range(659, 673), *range(674, 680),
     701, 710, 720, 725, 730, 735, 740, 745, 750, 755, 760, 765, 770, 775]
)  # fmt: skip

BEACONS = {"970": "sart", "972": "mob", "974": "epirb"}  # AIS-SART, man overboard, EPIRB-AIS
# Formats that carry a MID, and where it sits in the nine digits.
FORMATS = [("111", "sar_aircraft", 3), ("98", "craft", 2), ("99", "aton", 2), ("00", "coast_station", 2),
           ("0", "group", 1), ("8", "handheld", 1)]  # fmt: skip
PLACEHOLDERS = {"123456789", "987654321", "012345678"}


def kind(mmsi: int) -> str:
    """ship | craft | aton | coast_station | group | handheld | sar_aircraft | sart | mob | epirb | invalid"""
    if not 0 < mmsi < 1_000_000_000:
        return "invalid"
    digits = f"{mmsi:09d}"
    if digits[:3] in BEACONS:
        return BEACONS[digits[:3]]
    if len(set(digits)) == 1 or digits in PLACEHOLDERS:
        return "invalid"
    for prefix, name, at in FORMATS:
        if digits.startswith(prefix):
            return name if int(digits[at : at + 3]) in ALLOCATED_MIDS else "invalid"
    return "ship" if int(digits[:3]) in ALLOCATED_MIDS else "invalid"
