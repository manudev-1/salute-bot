"""Parse the CUP slots page into `Slot` objects.

This is the *parse* half of the scraper (build strategy D) — pure and
deterministic, so it can be tested offline against the real recon capture.

The slots arrive as a JSF/ICEfaces **partial-response**: an XML envelope whose
`<update id="…:availableAppointmentsContainer">` element wraps the real card
HTML inside a `<![CDATA[ … ]]>` block. That CDATA is the only parse target
(recon 2026-06-23). Python's `html.parser` drops CDATA content, so we extract
the block textually first, then parse its inner HTML.

Card fields are read by CSS class / structural position rather than by element
id: the JSF row ids (`…:j_idt699:0:` … `:16:`) are positional and renumber on
every render, so keying on them would be fragile.
"""

import logging
import re

from bs4 import BeautifulSoup

from salutebot.models import Slot

logger = logging.getLogger(__name__)

_MONTHS = {
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "settembre": 9,
    "ottobre": 10,
    "novembre": 11,
    "dicembre": 12,
}

# One <update id="…"> … <![CDATA[ inner html ]]> … </update> block.
_CDATA_UPDATE_RE = re.compile(
    r'<update\s+id="([^"]*)">\s*<!\[CDATA\[(.*?)\]\]>\s*</update>',
    re.DOTALL,
)


def parse_available_slots(markup: str) -> list[Slot]:
    """Parse a slots partial-response (or a raw inner fragment) into Slots.

    Order is preserved (the CUP "prima disponibilità" ordering). Cards missing
    the essential markup are skipped rather than raising.
    """
    inner = _extract_container_html(markup)
    soup = BeautifulSoup(inner, "html.parser")
    slots: list[Slot] = []
    for card in soup.select("div.disponibiliPanel"):
        slot = _parse_card(card)
        if slot is not None:
            slots.append(slot)
    logger.debug("Parsed %d available slots", len(slots))
    return slots


def _extract_container_html(markup: str) -> str:
    """Return the inner HTML of the availableAppointments update.

    Falls back to the raw markup when there is no CDATA envelope (e.g. a saved
    inner fragment), and to the largest CDATA block if the container id ever
    changes.
    """
    matches = _CDATA_UPDATE_RE.findall(markup)
    if not matches:
        return markup
    for update_id, cdata in matches:
        if update_id.endswith("availableAppointmentsContainer"):
            return cdata
    return max((cdata for _, cdata in matches), key=len)


def _parse_card(card) -> Slot | None:
    desc_el = card.select_one(".captionAppointment-desc")
    dateapp_el = card.select_one(".captionAppointment-dateApp")
    if desc_el is None or dateapp_el is None:
        return None

    prestazione_desc, prestazione_code, status = _parse_what(desc_el)
    iso_date, time = _parse_when(dateapp_el)
    if not iso_date or not time:
        return None

    struttura_raw, doctor_unit = _parse_struttura_and_doctor(card)
    cap = _cap_from_data_address(_data_address(card))
    address = _parse_address(card)

    return Slot(
        iso_date=iso_date,
        time=time,
        struttura=_normalize_struttura(struttura_raw),
        cap=cap,
        prestazione_code=prestazione_code,
        prestazione_desc=prestazione_desc,
        status=status,
        doctor_unit=doctor_unit,
        address=address,
    )


def _parse_what(desc_el) -> tuple[str, str, str]:
    """`.captionAppointment-desc` -> (descrizione, code, status).

    First span: "VISITA UROLOGICA DI CONTROLLO - 8901.20"; second: "(PRENOTABILE)".
    """
    spans = desc_el.find_all("span", recursive=False)
    what = spans[0].get_text(strip=True) if spans else ""
    if " - " in what:
        descrizione, code = what.rsplit(" - ", 1)
        descrizione, code = descrizione.strip(), code.strip()
    else:
        descrizione, code = what, ""
    status = spans[1].get_text(strip=True).strip("() ") if len(spans) > 1 else ""
    return descrizione, code, status


def _parse_when(dateapp_el) -> tuple[str, str]:
    """`.captionAppointment-dateApp` -> (iso_date, time).

    The two bold spans are the Italian date and the time; the middle span is the
    literal "alle ore".
    """
    bolds = dateapp_el.select(".fw-b")
    date_it = bolds[0].get_text(strip=True) if bolds else ""
    time = bolds[1].get_text(strip=True) if len(bolds) > 1 else ""
    return (_italian_date_to_iso(date_it) if date_it else ""), time


def _parse_struttura_and_doctor(card) -> tuple[str | None, str | None]:
    """First two text divs in the address info column: struttura, then doctor/unit.

    Excludes the `.unita-address` line and the "Visualizza mappa" button divs.
    """
    col = card.select_one("div.span6.mb-10")
    flex = col.select_one('div[style*="flex-grow"]') if col else None
    if flex is None:
        return None, None
    infos: list[str] = []
    for div in flex.find_all("div", recursive=False):
        classes = div.get("class") or []
        if "unita-address" in classes or div.select_one(".map-button-label"):
            continue
        txt = div.get_text(" ", strip=True)
        if txt:
            infos.append(txt)
    struttura = infos[0] if infos else None
    doctor = infos[1] if len(infos) > 1 else None
    return struttura, doctor


def _data_address(card) -> str | None:
    el = card.select_one(".captionAppointment-address")
    return el.get("data-address") if el else None


def _parse_address(card) -> str | None:
    """Human-readable address line from `.unita-address` (descriptive only)."""
    el = card.select_one(".unita-address")
    if el is None:
        return None
    txt = el.get_text(" ", strip=True)
    return txt or None


def _cap_from_data_address(data_address: str | None) -> str | None:
    """CAP = the token after the last comma in data-address; None when "null"."""
    if not data_address or "," not in data_address:
        return None
    cap = data_address.rsplit(",", 1)[1].strip()
    if not cap or cap.lower() == "null":
        return None
    return cap


def _normalize_struttura(struttura: str | None) -> str:
    """Whitespace-collapsed + upper-cased, per D16 (part of the natural key)."""
    if not struttura:
        return ""
    return " ".join(struttura.split()).upper()


def _italian_date_to_iso(text: str) -> str:
    """"Lunedì 22 Giugno 2026" -> "2026-06-22" (weekday dropped)."""
    tokens = text.replace("\xa0", " ").split()
    day = month = year = None
    for tok in tokens:
        low = tok.lower().strip(".,")
        if low in _MONTHS:
            month = _MONTHS[low]
        elif tok.isdigit():
            n = int(tok)
            if len(tok) == 4:
                year = n
            elif 1 <= n <= 31:
                day = n
    if day is None or month is None or year is None:
        raise ValueError(f"unparseable Italian date: {text!r}")
    return f"{year:04d}-{month:02d}-{day:02d}"
