"""Tipos de schema.org que declaran QUIEN esta detras de un sitio (decision 229).

`Organization` y `Person` con TODOS sus subtipos, en minusculas: un hotel
(`Hotel`), una clinica (`Dentist`) o una universidad (`CollegeOrUniversity`)
son una `Organization` para Google aunque el bloque no lo diga con esa
palabra. La lista sale de la jerarquia oficial
(`https://schema.org/version/latest/schemaorg-current-https-types.csv`,
descargada el 31-oct-2026, 190 tipos) recorriendo `subTypeOf`: escrita
a mano se quedaba corta, y cada tipo que faltara era un aviso falso.
"""

from __future__ import annotations

from typing import Any

TIPOS_DE_ENTIDAD: frozenset[str] = frozenset({
    "accountingservice", "adultentertainment", "airline", "amusementpark",
    "animalshelter", "archiveorganization", "artgallery", "attorney",
    "audiology", "autobodyshop", "autodealer", "autopartsstore", "autorental",
    "autorepair", "autowash", "automatedteller", "automotivebusiness",
    "bakery", "bankorcreditunion", "barorpub", "beautysalon",
    "bedandbreakfast", "bikestore", "bookstore", "bowlingalley", "brewery",
    "cafeorcoffeeshop", "campground", "casino", "childcare", "clothingstore",
    "collegeoruniversity", "comedyclub", "communityhealth", "computerstore",
    "consortium", "conveniencestore", "cooperative", "corporation",
    "covidtestingfacility", "dancegroup", "dayspa", "dentist",
    "departmentstore", "dermatology", "diagnosticlab", "dietnutrition",
    "distillery", "drycleaningorlaundry", "educationalorganization",
    "electrician", "electronicsstore", "elementaryschool", "emergency",
    "emergencyservice", "employmentagency", "entertainmentbusiness",
    "exercisegym", "fastfoodrestaurant", "financialservice", "firestation",
    "florist", "foodestablishment", "fundingagency", "fundingscheme",
    "furniturestore", "gardenstore", "gasstation", "generalcontractor",
    "geriatric", "golfcourse", "governmentoffice", "governmentorganization",
    "grocerystore", "gynecologic", "hvacbusiness", "hairsalon",
    "hardwarestore", "healthandbeautybusiness", "healthclub", "highschool",
    "hobbyshop", "homeandconstructionbusiness", "homegoodsstore", "hospital",
    "hostel", "hotel", "housepainter", "icecreamshop", "individualphysician",
    "insuranceagency", "internetcafe", "jewelrystore", "legalservice",
    "library", "librarysystem", "liquorstore", "localbusiness", "locksmith",
    "lodgingbusiness", "medicalbusiness", "medicalclinic",
    "medicalorganization", "mensclothingstore", "middleschool", "midwifery",
    "mobilephonestore", "motel", "motorcycledealer", "motorcyclerepair",
    "movierentalstore", "movietheater", "movingcompany", "musicgroup",
    "musicstore", "ngo", "nailsalon", "newsmediaorganization", "nightclub",
    "notary", "nursing", "obstetric", "officeequipmentstore", "oncologic",
    "onlinebusiness", "onlinemarketplace", "onlinestore", "ophthalmology",
    "optician", "optometric", "organization", "otolaryngologic",
    "outletstore", "patient", "pawnshop", "pediatric", "performinggroup",
    "person", "petstore", "pharmacy", "physician", "physiciansoffice",
    "physiotherapy", "plasticsurgery", "plumber", "podiatric",
    "policestation", "politicalparty", "postoffice", "preschool",
    "primarycare", "professionalservice", "project", "psychiatric",
    "publichealth", "publicswimmingpool", "radiostation", "realestateagent",
    "recyclingcenter", "researchorganization", "researchproject", "resort",
    "restaurant", "roofingcontractor", "school", "searchrescueorganization",
    "selfstorage", "shoestore", "shoppingcenter", "skiresort",
    "sportinggoodsstore", "sportsactivitylocation", "sportsclub",
    "sportsorganization", "sportsteam", "stadiumorarena", "store",
    "tattooparlor", "televisionstation", "tenniscomplex", "theatergroup",
    "tireshop", "touristinformationcenter", "toystore", "travelagency",
    "vacationrental", "veterinarycare", "wholesalestore", "winery",
    "workersunion"
})


def _nombre(tipo: Any) -> str:
    """`https://schema.org/Hotel`, `schema:Hotel` o `Hotel` -> `hotel`."""
    t = str(tipo or "").strip()
    for sep in ("/", "#", ":"):
        t = t.rsplit(sep, 1)[-1]
    return t.lower()


def tipos_de(raw: Any, _profundidad: int = 0) -> set[str]:
    """Todos los tipos de un bloque, tambien los anidados (`@graph`, el
    `publisher` de un `WebSite`) y en los tres formatos que guarda el spider:
    JSON-LD (`@type`), microdata (`type`) y RDFa expandido (`@type` en lista)."""
    encontrados: set[str] = set()
    if _profundidad > 20:
        return encontrados
    if isinstance(raw, dict):
        for clave in ("@type", "type"):
            valor = raw.get(clave)
            for t in (valor if isinstance(valor, list) else [valor]):
                if isinstance(t, str) and t.strip():
                    encontrados.add(_nombre(t))
        for valor in raw.values():
            if isinstance(valor, (dict, list)):
                encontrados |= tipos_de(valor, _profundidad + 1)
    elif isinstance(raw, list):
        for valor in raw:
            encontrados |= tipos_de(valor, _profundidad + 1)
    return encontrados


def declara_entidad(raw: Any) -> bool:
    return bool(tipos_de(raw) & TIPOS_DE_ENTIDAD)


__all__ = ["TIPOS_DE_ENTIDAD", "declara_entidad", "tipos_de"]
