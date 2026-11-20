"""Place-name data for the location gate: inside the home region vs. outside it.

The tables are engine data keyed by region id (``REGIONS``); the profile picks
one with ``[location] home_region`` in ``preferences.toml`` (default
``catalonia``, the only region shipped today).

Names are written naturally (accents, apostrophes) and folded at import by
:func:`fold_place` (lowercase, no accents, punctuation as spaces, same length
as the input so match offsets index the original text). Matching is
whole-word, longest name first, tolerant to any whitespace between words.

Precision over recall: a name that is also a common word or surname ("salt",
"sort", "roses", "leon", "granada", "palma", ...) is in ``STRUCTURED_ONLY`` and
only counts when it comes from the portal's structured location field, never
from free offer text. Unknown small towns are simply not listed (never read as
"outside Catalonia").
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Final


def fold_place(text: str) -> str:
    """Lowercase, strip accents and turn apostrophes/hyphens/dots into spaces,
    WITHOUT changing the length."""
    out: list[str] = []
    for char in text:
        base = unicodedata.normalize("NFD", char)[0]
        lowered = base.lower()
        char = lowered if len(lowered) == 1 else base
        out.append(" " if char in "'’`´-–.·" else char)
    return "".join(out)


def _names(csv: str) -> frozenset[str]:
    return frozenset(
        re.sub(r"\s+", " ", fold_place(n)).strip() for n in csv.split(",") if n.strip()
    )


# --- Catalonia ---------------------------------------------------------------

_REGIONS: Final[str] = (
    "Catalunya, Cataluña, Catalonia, Barcelona, Girona, Gerona, Lleida, Lérida, "
    "Tarragona, Área Metropolitana de Barcelona, Gran Barcelona, Costa Brava, "
    "Costa Dorada, Alt Pirineu"
)

_COMARCAS: Final[str] = (
    "Alt Camp, Alt Empordà, Alt Penedès, Alt Urgell, Alta Ribagorça, Anoia, Bages, "
    "Baix Camp, Baix Ebre, Baix Empordà, Baix Llobregat, Baix Penedès, Barcelonès, "
    "Berguedà, Cerdanya, Conca de Barberà, Garraf, Garrigues, Garrotxa, Gironès, "
    "Maresme, Montsià, Noguera, Osona, Pallars Jussà, Pallars Sobirà, Pla d'Urgell, "
    "Pla de l'Estany, Priorat, Ribera d'Ebre, Ripollès, Segarra, Segrià, Selva, "
    "Solsonès, Tarragonès, Terra Alta, Urgell, Val d'Aran, Vallès Occidental, "
    "Vallès Oriental, Moianès, Lluçanès, Vallès, Penedès, "
    # Spanish spellings
    "Alto Ampurdán, Bajo Ampurdán, Alto Penedés, Bajo Penedés, Bajo Llobregat, "
    "Vallés Occidental, Vallés Oriental, Valle de Arán"
)

_MUNICIPALITIES_BARCELONA: Final[str] = (
    "L'Hospitalet de Llobregat, Hospitalet de Llobregat, Hospitalet, Badalona, "
    "Terrassa, Tarrasa, Sabadell, Mataró, Santa Coloma de Gramenet, "
    "Santa Coloma de Gramanet, Cornellà de Llobregat, Cornellá de Llobregat, "
    "Cornellà, Cornellá, Sant Boi de Llobregat, Sant Boi, San Baudilio de Llobregat, "
    "Rubí, Manresa, Vilanova i la Geltrú, Vilanova y la Geltrú, Villanueva y Geltrú, "
    "Viladecans, Castelldefels, El Prat de Llobregat, Prat de Llobregat, "
    "Granollers, Cerdanyola del Vallès, Cerdañola del Vallés, Cerdanyola, "
    "Mollet del Vallès, Mollet del Vallés, Esplugues de Llobregat, "
    "Esplugas de Llobregat, Gavà, Gavá, Sant Cugat del Vallès, "
    "Sant Cugat del Vallés, Sant Cugat, San Cugat del Vallés, San Cugat, "
    "Sant Adrià de Besòs, San Adrián de Besós, Sant Feliu de Llobregat, "
    "San Feliu de Llobregat, Martorell, Sant Joan Despí, San Juan Despí, Vic, Vich, "
    "Igualada, Ripollet, Montcada i Reixac, Moncada y Reixac, Premià de Mar, "
    "Premiá de Mar, Vilafranca del Penedès, Villafranca del Penedés, "
    "Villafranca del Panadés, Sitges, Pineda de Mar, Calella, Malgrat de Mar, "
    "Sant Pere de Ribes, San Pedro de Ribas, Sant Vicenç dels Horts, "
    "San Vicente dels Horts, Olesa de Montserrat, Esparreguera, Molins de Rei, "
    "Santa Perpètua de Mogoda, Santa Perpetua de Mogoda, Parets del Vallès, "
    "Montornès del Vallès, La Garriga, Canovelles, Les Franqueses del Vallès, "
    "Caldes de Montbui, Sant Andreu de la Barca, San Andrés de la Barca, "
    "Sant Sadurní d'Anoia, San Sadurní de Noya, Cardedeu, "
    "Palau-solità i Plegamans, Sant Celoni, San Celoni, Canet de Mar, "
    "Arenys de Mar, Arenys de Munt, Vilassar de Mar, Vilassar de Dalt, "
    "Sant Pol de Mar, Cabrils, Premià de Dalt, Teià, El Masnou, Masnou, Alella, "
    "Tiana, Montgat, Badia del Vallès, Barberà del Vallès, Barbera del Vallés, "
    "Sant Quirze del Vallès, Castellar del Vallès, Polinyà, Sentmenat, "
    "Lliçà d'Amunt, Lliçà de Vall, Martorelles, La Roca del Vallès, "
    "Vilanova del Vallès, Sant Fost de Campsentelles, Llinars del Vallès, "
    "Sant Esteve de Palautordera, Tordera, Palafolls, Santa Susanna, "
    "Argentona, Dosrius, Cabrera de Mar, Òrrius, Sant Joan de Vilatorrada, "
    "Sant Fruitós de Bages, Navarcles, Sallent, Súria, Callús, Cardona, Navàs, "
    "Artés, Moià, Santpedor, Berga, Gironella, Puig-reig, Balsareny, "
    "Sant Vicenç de Castellet, Castellgalí, Òdena, Santa Margarida de Montbui, "
    "Vilanova del Camí, La Pobla de Claramunt, Capellades, Piera, Masquefa, "
    "Els Hostalets de Pierola, Calaf, Abrera, Begues, Castellví de Rosanes, "
    "Collbató, Corbera de Llobregat, Cervelló, La Palma de Cervelló, Pallejà, "
    "Sant Climent de Llobregat, Sant Esteve Sesrovires, Sant Just Desvern, "
    "San Justo Desvern, Torrelles de Llobregat, Santa Coloma de Cervelló, "
    "Vallirana, Olivella, Gelida, Sant Llorenç d'Hortons, Sant Pere de Riudebitlles, "
    "Subirats, Olèrdola, Cubelles, Cunit, Santa Margarida i els Monjos, "
    "Sant Martí Sarroca, Canyelles, Centelles, Balenyà, Manlleu, Torelló, "
    "Sant Hipòlit de Voltregà, Roda de Ter, Sant Quirze de Besora, "
    "Prats de Lluçanès, Gurb, Taradell, Sant Julià de Vilatorta, "
    "Sant Vicenç de Montalt, Sant Andreu de Llavaneres, Caldes d'Estrac, "
    "Sant Cebrià de Vallalta, Sant Iscle de Vallalta, Montmeló, "
    "Santa Eulàlia de Ronçana, Bigues i Riells, Sant Antoni de Vilamajor, "
    "Sant Pere de Vilamajor, Sant Feliu de Codines, Sant Llorenç Savall, "
    "Matadepera, Viladecavalls, Vacarisses, Ullastrell, Esplugues, Sant Climent, "
    "La Llagosta, L'Ametlla del Vallès, Santa Maria de Palautordera, "
    "Sant Pere de Torelló, Mediona, L'Arboç, Sant Jaume dels Domenys, "
    "Sant Vicenç de Calders, Bellvei, Coma-ruga, Banyeres del Penedès"
)

_MUNICIPALITIES_GIRONA: Final[str] = (
    "Figueres, Figueras, Blanes, Lloret de Mar, Lloret, Olot, Palafrugell, "
    "Sant Feliu de Guíxols, San Feliu de Guixols, Banyoles, Bañolas, Palamós, "
    "Ripoll, Santa Coloma de Farners, La Bisbal d'Empordà, Castelló d'Empúries, "
    "L'Escala, La Escala, Cassà de la Selva, Llagostera, Torroella de Montgrí, "
    "Sarrià de Ter, Anglès, Celrà, Santa Cristina d'Aro, Platja d'Aro, Calonge, "
    "Begur, Cadaqués, Puigcerdà, Llançà, Portbou, Sant Hilari Sacalm, Besalú, "
    "Camprodon, Maçanet de la Selva, Vidreres, Tossa de Mar, Hostalric, "
    "Fornells de la Selva, Sant Julià de Ramis, Bescanó, Sils, "
    "Riells i Viabrea, Caldes de Malavella, La Jonquera, Vilafant, "
    "Sant Joan de les Abadesses, Empuriabrava, Sant Pere Pescador, "
    "Vilablareix, Riudellots de la Selva, Cervià de Ter, Porqueres, "
    "Palau-saverdera, Vilamalla, Cabanes, Pals, Santa Pau, Les Preses, "
    "Sant Gregori, Llambilles, Arbúcies, Bordils, L'Estartit, Sant Antoni de Calonge, "
    "Sant Feliu de Pallerols, Navata, Palol d'Onyar"
)

_MUNICIPALITIES_LLEIDA: Final[str] = (
    "Balaguer, Tàrrega, Tarrega, Mollerussa, La Seu d'Urgell, Seo de Urgel, "
    "Cervera, Tremp, Solsona, Les Borges Blanques, Alpicat, Almacelles, Vielha, "
    "Agramunt, Bellpuig, Guissona, Pont de Suert, Bellvís, Torrefarrera, "
    "Alcarràs, Alguaire, Artesa de Segre, Seròs, Alcoletge, La Pobla de Segur, "
    "Juneda, Almenar, Torres de Segre, Soses, Alfarràs, Bell-lloc d'Urgell, "
    "Isona, Oliana, Bellver de Cerdanya, Esterri d'Àneu, Linyola, "
    "Ivars d'Urgell, Sidamon, Torregrossa, Vilanova de la Barca, Vila-sana, "
    "Arbeca, Ponts, Camarasa, Albatàrrec, Corbins, Sunyer, Castelldans, Rosselló, Térmens, Torà, Sant Guim de Freixenet"
)

_MUNICIPALITIES_TARRAGONA: Final[str] = (
    "Reus, El Vendrell, Cambrils, Valls, Tortosa, Salou, Vila-seca, Vilaseca, "
    "Amposta, Calafell, Segur de Calafell, Constantí, Montblanc, Falset, "
    "Mont-roig del Camp, L'Hospitalet de l'Infant, Roda de Berà, Torredembarra, "
    "Altafulla, Santa Oliva, La Selva del Camp, Alcover, Vandellòs, "
    "L'Ametlla de Mar, Deltebre, L'Aldea, Ulldecona, Móra d'Ebre, Mora d'Ebre, "
    "Gandesa, Flix, Ascó, La Canonja, Els Pallaresos, Roquetes, Santa Bàrbara, "
    "Alcanar, Sant Carles de la Ràpita, San Carlos de la Rápita, La Sénia, "
    "Vinyols i els Arcs, Riudoms, Vilallonga del Camp, Perafort, El Morell, "
    "La Pobla de Mafumet, Creixell, Cabra del Camp, Batea, Prades, Vimbodí, "
    "La Pobla de Montornès, La Riera de Gaià, Ferran, L'Espluga de Francolí, L'Ampolla, El Catllar, Tamarit, Xerta, Aldover, "
    "Sant Jaume d'Enveja, Camarles, Tivenys, El Perelló, Móra la Nova"
)

_CATALAN_AMBIGUOUS: Final[str] = "Salt, Sort, Roses, Tona, Gurb, Seva"

CATALONIA: Final[frozenset[str]] = _names(
    f"{_REGIONS}, {_COMARCAS}, {_MUNICIPALITIES_BARCELONA}, "
    f"{_MUNICIPALITIES_GIRONA}, {_MUNICIPALITIES_LLEIDA}, "
    f"{_MUNICIPALITIES_TARRAGONA}, {_CATALAN_AMBIGUOUS}"
)

# --- Outside Catalonia ----------------------------------------------------------

_SPANISH_COMMUNITIES: Final[str] = (
    "Andalucía, Aragón, Principado de Asturias, Asturias, Cantabria, Castilla y León, "
    "Castilla-La Mancha, Comunidad de Madrid, Comunidad Valenciana, "
    "Comunitat Valenciana, Región de Murcia, Extremadura, Galicia, Islas Baleares, "
    "Illes Balears, Baleares, Islas Canarias, Canarias, La Rioja, "
    "Comunidad Foral de Navarra, País Vasco, Euskadi, Ceuta, Melilla"
)

_SPANISH_PROVINCES: Final[str] = (
    "Álava, Araba, Albacete, Alicante, Alacant, Almería, Ávila, Badajoz, Cádiz, "
    "Castellón, Castellón de la Plana, Ciudad Real, Córdoba, A Coruña, La Coruña, "
    "Coruña, Cuenca, Guadalajara, Granada, Guipúzcoa, Gipuzkoa, Huelva, Huesca, "
    "Jaén, León, Lugo, Madrid, Málaga, Murcia, Navarra, Ourense, Orense, Palencia, "
    "Pontevedra, Salamanca, Segovia, Sevilla, Soria, Teruel, Toledo, Valencia, "
    "València, Valladolid, Vizcaya, Bizkaia, Zamora, Zaragoza, Las Palmas, "
    "Santa Cruz de Tenerife, Burgos, Mallorca, Menorca, Ibiza, Eivissa, "
    "Formentera, Tenerife, Gran Canaria, Lanzarote, Fuerteventura"
)

_SPANISH_CITIES: Final[str] = (
    "Bilbao, Vitoria, Vitoria-Gasteiz, Gasteiz, San Sebastián, Donostia, Pamplona, "
    "Iruña, Logroño, Oviedo, Gijón, Avilés, Vigo, Santiago de Compostela, "
    "Palma de Mallorca, Palma, Las Palmas de Gran Canaria, Mérida, Cáceres, "
    "Santander, Talavera de la Reina, Alcalá de Henares, Alcobendas, Alcorcón, "
    "Getafe, Leganés, Móstoles, Fuenlabrada, Parla, Torrejón de Ardoz, "
    "Pozuelo de Alarcón, Las Rozas, Majadahonda, Rivas-Vaciamadrid, Coslada, "
    "San Sebastián de los Reyes, Boadilla del Monte, Tres Cantos, "
    "Elche, Benidorm, Gandía, Torrevieja, Orihuela, Cartagena, Lorca, Jerez, "
    "Jerez de la Frontera, Algeciras, Marbella, Estepona, Fuengirola, "
    "Torremolinos, Benalmádena, Dos Hermanas, Alcalá de Guadaíra, Torrelavega, "
    "Ponferrada, Telde, Maspalomas, Vila-real, Sagunto, Paterna, Torrent, Alcoy, "
    "Getxo, Barakaldo, Portugalete, Irún, Eibar, Erandio, Leioa, Basauri, Ferrol"
)

_FOREIGN: Final[str] = (
    # countries
    "Portugal, France, Francia, Germany, Alemania, Deutschland, United Kingdom, "
    "Reino Unido, UK, England, Inglaterra, Scotland, Escocia, Ireland, Irlanda, "
    "Italy, Italia, Netherlands, Holanda, Países Bajos, Belgium, Bélgica, "
    "Switzerland, Suiza, Austria, Poland, Polonia, Sweden, Suecia, Denmark, "
    "Dinamarca, Norway, Noruega, Finland, Finlandia, Czech Republic, Chequia, "
    "Romania, Rumanía, Rumania, Greece, Grecia, Hungary, Hungría, Bulgaria, "
    "Luxembourg, Luxemburgo, Ukraine, Ucrania, Turkey, Turquía, USA, "
    "United States, Estados Unidos, EE UU, EEUU, Canada, Canadá, Mexico, México, "
    "Argentina, Colombia, Chile, Perú, Peru, Brazil, Brasil, India, China, Japan, "
    "Japón, Singapore, Singapur, Australia, Dubai, Dubái, UAE, Emiratos Árabes, "
    "Israel, Marruecos, Morocco, Uruguay, Costa Rica, Panamá, Ecuador, "
    # cities
    "London, Londres, Paris, París, Berlin, Berlín, Lisbon, Lisboa, Porto, Oporto, "
    "Amsterdam, Ámsterdam, Dublin, Dublín, Munich, München, Múnich, Milan, Milano, "
    "Milán, Rome, Brussels, Bruselas, Zurich, Zúrich, Geneva, Ginebra, Frankfurt, "
    "Fráncfort, Hamburg, Hamburgo, Stockholm, Estocolmo, Copenhagen, Copenhague, "
    "Vienna, Viena, Prague, Praga, Warsaw, Varsovia, Krakow, Cracovia, Budapest, "
    "Bucharest, Bucarest, Athens, Atenas, Helsinki, Oslo, Manchester, Edinburgh, "
    "Edimburgo, Cambridge, Oxford, Birmingham, Lyon, Marseille, Marsella, "
    "Toulouse, Niza, Turin, Turín, Nueva York, New York, San Francisco, Boston, "
    "Seattle, Chicago, Los Angeles, Toronto, Vancouver, Mexico City, "
    "Ciudad de México, Bogotá, Buenos Aires, Santiago de Chile, São Paulo, "
    "Sao Paulo, Tel Aviv, Bangalore, Bengaluru, Tokyo, Tokio, Cologne, "
    "Düsseldorf, Dusseldorf, Stuttgart, Eindhoven, Rotterdam, Casablanca, "
    "Tánger, Tangier, Rabat, Montevideo, Medellín"
)

OUTSIDE: Final[frozenset[str]] = _names(
    f"{_SPANISH_COMMUNITIES}, {_SPANISH_PROVINCES}, {_SPANISH_CITIES}, {_FOREIGN}"
)

# Names that are also common words/surnames/first names (or Catalan towns that
# collide with an English word): counted only from the structured location.
_STRUCTURED_ONLY_CSV: Final[str] = (
    "Selva, Noguera, Segarra, Garrigues, Terra Alta, Seva, Ponts, Cabanes, Pals, "
    "Sils, Flix, Prades, Ferran, Urgell, Vallès, Penedès, Ripoll, "
    f"{_CATALAN_AMBIGUOUS}, "
    # outside
    "Leon, Avila, Burgos, Cuenca, Zamora, Lugo, Granada, Palma, Santander, "
    "Cordoba, Soria, Jerez, Torrent, Parla, Cambridge, Oxford, Rome, "
    # Spanish words: "se usa", "la china", "chile" (pepper), the first name
    "USA, China, Chile, Israel"
)
STRUCTURED_ONLY: Final[frozenset[str]] = _names(_STRUCTURED_ONLY_CSV)

# A name listed on both sides is ambiguous: the Catalan reading wins (no false
# FAIL). Currently empty by construction; pinned by a test.
OUTSIDE_RESOLVED: Final[frozenset[str]] = OUTSIDE - CATALONIA


@dataclass(frozen=True)
class PlaceHit:
    """One place mention: span in the folded (== original-length) text."""

    start: int
    end: int
    name: str
    inside: bool


def _compile(names: frozenset[str]) -> re.Pattern[str]:
    ordered = sorted(names, key=lambda n: (-len(n), n))
    body = "|".join(r"\s+".join(re.escape(w) for w in n.split(" ")) for n in ordered)
    return re.compile(r"(?<![a-z0-9])(?:" + body + r")(?![a-z0-9])")


@dataclass(frozen=True)
class Region:
    """The place tables of one home region.

    ``label`` is the Spanish name used in the gate's reason text
    ("fuera de <label>").
    """

    id: str
    label: str
    inside: frozenset[str]
    structured_only: frozenset[str]
    pattern: re.Pattern[str]


class UnknownRegionError(ValueError):
    """``home_region`` names no region in ``REGIONS``."""


def make_region(
    region_id: str,
    *,
    label: str,
    inside: str,
    outside: str,
    structured_only: str = "",
) -> Region:
    """A region from comma-separated place names (folded like the built-ins)."""
    inside_names = _names(inside)
    outside_names = _names(outside) - inside_names
    return Region(
        id=region_id,
        label=label,
        inside=inside_names,
        structured_only=_names(structured_only),
        pattern=_compile(inside_names | outside_names),
    )


CATALONIA_REGION: Final[Region] = Region(
    id="catalonia",
    label="Cataluña",
    inside=CATALONIA,
    structured_only=STRUCTURED_ONLY,
    pattern=_compile(CATALONIA | OUTSIDE_RESOLVED),
)

REGIONS: Final[dict[str, Region]] = {CATALONIA_REGION.id: CATALONIA_REGION}


def get_region(region_id: str) -> Region:
    """The region registered under ``region_id``; a clear error otherwise."""
    try:
        return REGIONS[region_id]
    except KeyError:
        known = ", ".join(sorted(REGIONS))
        raise UnknownRegionError(
            f"unknown home_region {region_id!r} (known regions: {known})"
        ) from None


def find_places(
    folded: str, *, structured: bool = False, region: Region = CATALONIA_REGION
) -> list[PlaceHit]:
    """Place mentions in ``folded`` (already passed through :func:`fold_place`).

    Names in the region's ``structured_only`` set are skipped unless
    ``structured`` (the text is the portal's location field, where such a name
    is unambiguous). ``inside`` means inside the region.
    """
    hits: list[PlaceHit] = []
    for m in region.pattern.finditer(folded):
        name = re.sub(r"\s+", " ", m.group())
        if not structured and name in region.structured_only:
            continue
        hits.append(PlaceHit(m.start(), m.end(), name, name in region.inside))
    return hits


# --- Generic home (onboarding-v2 S6): any city or country, by NAME -------------

# Country names as offers write them (ES/EN/CA/native), one canonical id each.
_COUNTRY_NAMES: Final[dict[str, str]] = {
    "spain": "España, Spain, Espanya, Espagne, Spanien",
    "portugal": "Portugal",
    "france": "France, Francia, França, Frankreich",
    "germany": "Germany, Alemania, Alemanya, Deutschland, Allemagne",
    "italy": "Italy, Italia, Itàlia, Italie",
    "uk": (
        "United Kingdom, Reino Unido, Regne Unit, UK, Great Britain, "
        "Gran Bretaña, England, Inglaterra, Scotland, Escocia"
    ),
    "ireland": "Ireland, Irlanda",
    "netherlands": "Netherlands, The Netherlands, Países Bajos, Holanda, Nederland",
    "belgium": "Belgium, Bélgica, Belgique, België",
    "switzerland": "Switzerland, Suiza, Suïssa, Schweiz, Suisse",
    "austria": "Austria, Österreich",
    "poland": "Poland, Polonia, Polska",
    "usa": "United States, Estados Unidos, USA, EEUU, EE UU",
    "canada": "Canada, Canadá",
    "mexico": "Mexico, México",
    "argentina": "Argentina",
    "colombia": "Colombia",
    "chile": "Chile",
}
COUNTRY_ALIASES: Final[dict[str, frozenset[str]]] = {
    key: _names(csv) for key, csv in _COUNTRY_NAMES.items()
}
# Every place of the shipped tables is in Spain (except the foreign list).
_SPAIN_PLACES: Final[frozenset[str]] = CATALONIA | _names(
    f"{_SPANISH_COMMUNITIES}, {_SPANISH_PROVINCES}, {_SPANISH_CITIES}"
)
_KNOWN_PLACES: Final[frozenset[str]] = (
    CATALONIA | OUTSIDE | frozenset().union(*COUNTRY_ALIASES.values())
)


def _home_name(text: str | None) -> str | None:
    """``"Madrid, España"`` -> ``"Madrid"``; blank -> None."""
    name = (text or "").split(",")[0].strip()
    return name or None


def _country_names(country: str) -> frozenset[str]:
    """All the names of ``country`` (its own folded name when not listed)."""
    own = _names(country)
    for aliases in COUNTRY_ALIASES.values():
        if own & aliases:
            return aliases | own
    return own


def generic_region(
    city: str | None, country: str | None, home: Region
) -> Region | None:
    """The by-name region of ``[location] city``/``country``, or ``None`` when
    the detailed ``home`` region applies.

    ``home`` applies when neither is set (every existing profile) and when the
    city is one of ``home``'s own places (a Barcelona user keeps the detailed
    Catalonia gate). Otherwise the region's inside is the city (or, with no
    city, every name of the country, plus the shipped Spanish places for
    Spain) and its outside is every other known place and country name; the
    home country's own names are no place at all when a city is set, so
    "Madrid, Spain" is just Madrid. Names that contain the city ("Comunidad de
    Madrid") are dropped so the city itself matches. ``label`` is the name
    as the user wrote it.
    """
    city = _home_name(city)
    country = _home_name(country)
    city_names = _names(city) if city else frozenset()
    if city_names and city_names <= home.inside:
        return None
    if not city_names and country is None:
        return None
    own = _country_names(country) if country else frozenset()
    if city_names:
        inside = city_names
        words = [
            re.compile(rf"(?<![a-z0-9]){re.escape(c)}(?![a-z0-9])") for c in city_names
        ]
        outside = {
            n for n in _KNOWN_PLACES - own if not any(w.search(n) for w in words)
        }
    else:
        inside = own | (
            _SPAIN_PLACES if own & COUNTRY_ALIASES["spain"] else frozenset()
        )
        outside = set(_KNOWN_PLACES)
    label = city or country or ""
    return Region(
        id="generic",
        label=label,
        inside=frozenset(inside),
        structured_only=STRUCTURED_ONLY - inside,
        pattern=_compile(inside | frozenset(outside)),
    )
