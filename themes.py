"""Homelab easter-egg themes for the discovery CSV generator.

Each theme is a function that takes a CSV-field name and (optionally) a
device class, and returns a schema-compliant value. Themes only override
fields they have opinions about; the caller falls back to a default for
anything not themed.

Themes:
  darknet  *.darknetian.com hostnames, "radio waves..." vibes, real homelab vendors
  bsg      Battlestar Galactica callsigns (Vipers, Galactica, Cylon-detector)
  tng      Star Trek TNG decks and holodecks
  random   Faker-driven, schema-compliant but otherwise neutral
"""
import random


CSV_DEVICE_CLASSES = ("l3_switch", "wireless_ap", "virtual_host", "vm", "endpoint")


# --- Darknet (real homelab flavor) ----------------------------------------

_DARKNET_HOSTS = ["e300", "ddi", "ni", "ns1", "tr", "gm", "esxi2",
                  "ap-lobby-01", "ap-rack-02", "starbuck"]


def darknet(field, device_class="l3_switch", rng=None):
    rng = rng or random.Random()
    if field == "discovered_name":
        return f"{rng.choice(_DARKNET_HOSTS)}.darknetian.com"
    if field == "device_location":
        return rng.choice(["radio waves...", "Rack 3 U7", "Lobby ceiling",
                           "homelab basement", "garage shelf"])
    if field == "device_contact":
        return "nic@infoblox.com"
    if field == "tenant":
        return "homelab"
    if field == "discoverer":
        return "Network Insight"
    if field == "attached_device_name":
        return rng.choice([h + ".darknetian.com" for h in _DARKNET_HOSTS])
    if field == "device_vendor":
        return rng.choice(["Infoblox", "Aerohive", "Aruba", "Dell",
                           "VMWare", "LinkSys"])
    return None


# --- Battlestar Galactica -------------------------------------------------

_BSG_CALLSIGNS = ["starbuck", "apollo", "boomer", "athena", "racetrack",
                  "hot-dog", "kat", "helo", "duck", "narcho"]


def bsg(field, device_class="l3_switch", rng=None):
    rng = rng or random.Random()
    if field == "discovered_name":
        return f"{rng.choice(_BSG_CALLSIGNS)}-{rng.randint(1, 12):02d}.galactica"
    if field == "device_location":
        return rng.choice(["Hangar Deck", "CIC", "FTL Drive Room",
                           "Engineering", "Officers' Quarters", "Sickbay"])
    if field == "device_contact":
        return "adama@galactica.mil"
    if field == "device_vendor":
        return "Colonial Fleet"
    if field == "device_model":
        return rng.choice(["Viper Mk II", "Viper Mk VII", "Raptor",
                           "Heavy Raider", "Cylon Centurion Mk V"])
    if field == "tenant":
        return "colonial-fleet"
    if field == "discoverer":
        return "Galen Tyrol DEI"
    return None


# --- Star Trek TNG --------------------------------------------------------

_TNG_NAMES = ["picard", "riker", "data", "geordi", "worf", "troi",
              "crusher", "wesley", "barclay"]


def tng(field, device_class="l3_switch", rng=None):
    rng = rng or random.Random()
    if field == "discovered_name":
        return f"{rng.choice(_TNG_NAMES)}.enterprise-d.starfleet"
    if field == "device_location":
        return f"Deck {rng.randint(1, 42)} " + rng.choice([
            "Holodeck 3", "Ten Forward", "Main Engineering",
            "Bridge", "Quarters", "Cargo Bay 2",
        ])
    if field == "device_contact":
        return "lcars@enterprise-d.starfleet"
    if field == "device_vendor":
        return "Starfleet"
    if field == "device_model":
        return rng.choice(["LCARS-4001", "Tricorder Mk VII",
                           "Type-2 Phaser", "Isolinear Chip"])
    if field == "tenant":
        return "starfleet"
    if field == "discoverer":
        return "Geordi La Forge"
    return None


# --- Faker fallback -------------------------------------------------------

def random_compliant(field, device_class="l3_switch", rng=None):
    """Returns None — caller's default generator handles everything.
    This theme exists as a no-op marker so --theme random is a valid flag."""
    return None


THEMES = {
    "darknet": darknet,
    "bsg": bsg,
    "tng": tng,
    "random": random_compliant,
}


def apply(theme_name, field, device_class="l3_switch", rng=None):
    fn = THEMES.get(theme_name)
    if not fn:
        return None
    return fn(field, device_class, rng=rng)
