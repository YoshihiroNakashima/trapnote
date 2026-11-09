"""種リストの上下関係（属 → 科 → 目 → 綱）を学名から決めるための表

種リストの各行の「上位」（species.csv の parent 列）が空なら、学名（name_sci。なければ speciesnet 列）の属・科・目から
この表をたどって、種リストにある一番近い上位の行を親にする（ニホンイタチ Mustela itatsi → イタチ属 → イタチ科 → 食肉目 → 哺乳類）。
表にない属や科は、種リストの窓で「上位」を指定すればよい。分類は SpeciesNet のラベル（綱;目;科;属;種）に合わせた
（偶蹄目は cetartiodactyla、食虫類は eulipotyphla）。日本の哺乳類と、カメラによく写る鳥を中心に載せる。"""
from __future__ import annotations

# 目 → 綱
_ORDERS = {
    "mammalia": "carnivora cetartiodactyla perissodactyla primates rodentia lagomorpha eulipotyphla chiroptera pholidota diprotodontia "
                "didelphimorphia cingulata pilosa proboscidea scandentia dasyuromorphia peramelemorphia hyracoidea tubulidentata macroscelidea afrosoricida",
    "aves": "passeriformes galliformes columbiformes accipitriformes falconiformes strigiformes piciformes pelecaniformes ciconiiformes anseriformes "
            "charadriiformes gruiformes cuculiformes coraciiformes bucerotiformes caprimulgiformes apodiformes psittaciformes suliformes "
            "podicipediformes otidiformes struthioniformes casuariiformes tinamiformes trogoniformes",
    "reptilia": "squamata testudines crocodylia",
    "amphibia": "anura caudata gymnophiona",
}
# 科 → 目
_FAMILIES = {
    "carnivora": "mustelidae canidae ursidae procyonidae viverridae felidae herpestidae mephitidae ailuridae hyaenidae otariidae phocidae "
                 "odobenidae eupleridae prionodontidae nandiniidae",
    "cetartiodactyla": "cervidae suidae bovidae moschidae tragulidae tayassuidae camelidae giraffidae antilocapridae hippopotamidae",
    "perissodactyla": "equidae rhinocerotidae tapiridae",
    "primates": "cercopithecidae hominidae hylobatidae lorisidae galagidae cebidae atelidae pitheciidae aotidae callitrichidae lemuridae "
                "indriidae cheirogaleidae tarsiidae",
    "rodentia": "muridae cricetidae sciuridae gliridae dipodidae hystricidae erethizontidae caviidae castoridae spalacidae nesomyidae "
                "echimyidae dasyproctidae cuniculidae myocastoridae chinchillidae geomyidae heteromyidae anomaluridae pedetidae "
                "thryonomyidae bathyergidae aplodontiidae platacanthomyidae",
    "lagomorpha": "leporidae ochotonidae",
    "eulipotyphla": "soricidae talpidae erinaceidae solenodontidae",
    "chiroptera": "vespertilionidae rhinolophidae pteropodidae molossidae miniopteridae hipposideridae phyllostomidae emballonuridae",
    "pholidota": "manidae",
    "diprotodontia": "macropodidae phalangeridae vombatidae phascolarctidae petauridae pseudocheiridae potoroidae",
    "didelphimorphia": "didelphidae",
    "cingulata": "dasypodidae chlamyphoridae",
    "pilosa": "myrmecophagidae bradypodidae megalonychidae",
    "proboscidea": "elephantidae",
    "scandentia": "tupaiidae",
    "galliformes": "phasianidae odontophoridae numididae megapodiidae cracidae",
    "columbiformes": "columbidae",
    "passeriformes": "corvidae turdidae muscicapidae paridae pycnonotidae emberizidae fringillidae motacillidae passeridae sturnidae laniidae "
                     "aegithalidae cettiidae zosteropidae hirundinidae sittidae troglodytidae alaudidae phylloscopidae regulidae certhiidae "
                     "oriolidae campephagidae monarchidae dicruridae prunellidae cinclidae bombycillidae paradoxornithidae timaliidae "
                     "leiothrichidae acrocephalidae locustellidae cisticolidae ploceidae estrildidae pittidae",
    "accipitriformes": "accipitridae pandionidae cathartidae",
    "falconiformes": "falconidae",
    "strigiformes": "strigidae tytonidae",
    "piciformes": "picidae ramphastidae",
    "pelecaniformes": "ardeidae threskiornithidae",
    "ciconiiformes": "ciconiidae",
    "anseriformes": "anatidae",
    "charadriiformes": "scolopacidae charadriidae laridae recurvirostridae haematopodidae jacanidae turnicidae",
    "gruiformes": "rallidae gruidae",
    "cuculiformes": "cuculidae",
    "coraciiformes": "alcedinidae meropidae coraciidae",
    "bucerotiformes": "upupidae bucerotidae",
    "caprimulgiformes": "caprimulgidae",
    "apodiformes": "apodidae trochilidae",
    "psittaciformes": "psittacidae cacatuidae psittaculidae",
    "suliformes": "phalacrocoracidae",
    "podicipediformes": "podicipedidae",
    "squamata": "colubridae viperidae elapidae lacertidae scincidae gekkonidae agamidae varanidae iguanidae",
    "testudines": "emydidae geoemydidae testudinidae trionychidae",
    "anura": "ranidae bufonidae hylidae rhacophoridae microhylidae dicroglossidae",
    "caudata": "salamandridae hynobiidae cryptobranchidae plethodontidae",
}
# 属 → 科
_GENERA = {
    "mustelidae": "mustela martes meles lutra enhydra gulo neovison neogale arctonyx melogale taxidea mellivora lontra aonyx eira galictis vormela ictonyx",
    "canidae": "nyctereutes vulpes canis cuon urocyon lycaon chrysocyon cerdocyon lycalopex speothos otocyon",
    "ursidae": "ursus helarctos melursus tremarctos ailuropoda",
    "procyonidae": "procyon nasua potos bassariscus",
    "viverridae": "paguma viverricula viverra paradoxurus arctictis genetta civettictis hemigalus arctogalidia",
    "felidae": "felis prionailurus lynx panthera puma neofelis pardofelis catopuma otocolobus leopardus acinonyx caracal leptailurus herpailurus",
    "herpestidae": "herpestes urva mungos ichneumia atilax suricata helogale galerella cynictis",
    "mephitidae": "mephitis spilogale conepatus mydaus",
    "suidae": "sus phacochoerus potamochoerus babyrousa hylochoerus",
    "cervidae": "cervus muntiacus capreolus rusa axis dama alces rangifer odocoileus hydropotes elaphurus elaphodus mazama blastocerus pudu",
    "bovidae": "capricornis capra ovis bos bubalus naemorhedus rupicapra budorcas cephalophus tragelaphus syncerus gazella antilope hemitragus "
               "ammotragus pseudois oreamnos ovibos kobus sylvicapra philantomba madoqua aepyceros",
    "moschidae": "moschus",
    "tragulidae": "tragulus",
    "equidae": "equus",
    "cercopithecidae": "macaca chlorocebus papio cercopithecus colobus trachypithecus semnopithecus presbytis nasalis rhinopithecus erythrocebus",
    "hominidae": "homo pan gorilla pongo",
    "leporidae": "lepus oryctolagus pentalagus sylvilagus",
    "ochotonidae": "ochotona",
    "sciuridae": "sciurus pteromys petaurista tamias callosciurus tamiasciurus glaucomys marmota spermophilus urocitellus xerus funambulus "
                 "dremomys ratufa sundasciurus otospermophilus cynomys",
    "gliridae": "glirulus glis muscardinus eliomys dryomys graphiurus",
    "muridae": "apodemus rattus mus micromys tokudaia diplothrix bandicota niviventer leopoldamys maxomys chiropodomys berylmys mastomys praomys arvicanthis",
    "cricetidae": "myodes craseomys eothenomys microtus alexandromys clethrionomys ondatra peromyscus neotoma sigmodon cricetulus phodopus "
                  "mesocricetus arvicola lemmus cricetus",
    "hystricidae": "hystrix atherurus trichys",
    "castoridae": "castor",
    "myocastoridae": "myocastor",
    "caviidae": "hydrochoerus cavia dolichotis",
    "dasyproctidae": "dasyprocta",
    "cuniculidae": "cuniculus",
    "erethizontidae": "erethizon coendou",
    "soricidae": "sorex crocidura chimarrogale suncus neomys blarina anourosorex episoriculus",
    "talpidae": "mogera urotrichus dymecodon euroscaptor talpa scalopus condylura oreoscaptor",
    "erinaceidae": "erinaceus atelerix hemiechinus echinosorex hylomys",
    "vespertilionidae": "myotis pipistrellus nyctalus eptesicus murina plecotus barbastella vespertilio lasiurus",
    "rhinolophidae": "rhinolophus",
    "pteropodidae": "pteropus cynopterus rousettus",
    "miniopteridae": "miniopterus",
    "molossidae": "tadarida",
    "manidae": "manis smutsia phataginus",
    "phasianidae": "phasianus syrmaticus bambusicola coturnix gallus pavo lagopus tetrastes perdix alectoris francolinus lophura polyplectron "
                   "arborophila chrysolophus tragopan meleagris",
    "columbidae": "streptopelia columba treron chalcophaps spilopelia",
    "corvidae": "corvus garrulus cyanopica pica nucifraga urocissa perisoreus cyanocitta",
    "turdidae": "turdus zoothera geokichla",
    "muscicapidae": "tarsiger larvivora luscinia phoenicurus muscicapa ficedula cyanoptila monticola saxicola erithacus calliope copsychus myophonus enicurus niltava cyornis",
    "paridae": "parus poecile periparus sittiparus cyanistes pardaliparus",
    "pycnonotidae": "hypsipetes pycnonotus ixos",
    "emberizidae": "emberiza",
    "fringillidae": "eophona coccothraustes fringilla chloris carduelis pyrrhula uragus carpodacus spinus loxia",
    "motacillidae": "motacilla anthus",
    "passeridae": "passer",
    "sturnidae": "sturnus spodiopsar acridotheres agropsar",
    "laniidae": "lanius",
    "aegithalidae": "aegithalos",
    "cettiidae": "horornis urosphena cettia",
    "zosteropidae": "zosterops",
    "troglodytidae": "troglodytes",
    "sittidae": "sitta",
    "hirundinidae": "hirundo cecropis delichon",
    "leiothrichidae": "garrulax leiothrix",
    "picidae": "dendrocopos picus dryocopus yungipicus picoides dendrocoptes leiopicus jynx sapheopipo",
    "accipitridae": "accipiter buteo butastur milvus nisaetus spilornis aquila haliaeetus circus pernis spizaetus",
    "falconidae": "falco",
    "strigidae": "strix otus bubo ninox asio athene glaucidium ketupa",
    "tytonidae": "tyto",
    "ardeidae": "ardea egretta nycticorax gorsachius butorides bubulcus ixobrychus ardeola botaurus",
    "anatidae": "anas aix mergus aythya anser cygnus mareca spatula",
    "scolopacidae": "scolopax gallinago tringa actitis calidris numenius",
    "charadriidae": "charadrius vanellus",
    "rallidae": "gallinula fulica amaurornis porzana rallus zapornia gallirallus hypotaenidia",
    "cuculidae": "cuculus hierococcyx",
    "alcedinidae": "alcedo halcyon megaceryle",
    "upupidae": "upupa",
    "caprimulgidae": "caprimulgus",
    "apodidae": "apus hirundapus",
}
# 別名（古い分類・表記ゆれ） → 表の名前
ALIASES = {"artiodactyla": "cetartiodactyla", "soricomorpha": "eulipotyphla", "erinaceomorpha": "eulipotyphla", "insectivora": "eulipotyphla",
           "urodela": "caudata", "lipotyphla": "eulipotyphla", "chelonia": "testudines"}

PARENT: dict[str, str] = {}      # 分類群 → 1 つ上（属 → 科、科 → 目、目 → 綱）
LEVEL: dict[str, str] = {}       # 分類群 → genus / family / order / class
for _cls, _txt in _ORDERS.items():
    LEVEL[_cls] = "class"
    for _o in _txt.split():
        PARENT[_o], LEVEL[_o] = _cls, "order"
for _o, _txt in _FAMILIES.items():
    for _f in _txt.split():
        PARENT[_f], LEVEL[_f] = _o, "family"
for _f, _txt in _GENERA.items():
    for _g in _txt.split():
        PARENT[_g], LEVEL[_g] = _f, "genus"

# 種リストの「階級」列（rank）→ 階級。日本語と英語の両方を受ける
RANKS = {"種": "species", "亜種": "species", "属": "genus", "亜属": "genus", "族": "family", "亜科": "family", "科": "family", "上科": "family",
         "目": "order", "亜目": "order", "綱": "class", "species": "species", "subspecies": "species", "genus": "genus", "family": "family",
         "order": "order", "class": "class"}
LEVEL_ORDER = {"species": 0, "genus": 1, "family": 2, "order": 3, "class": 4}


def norm_taxon(tok: str) -> str:
    t = (tok or "").strip().lower()
    return ALIASES.get(t, t)


def level_of(taxon: str, rank_hint: str = "") -> str | None:
    """1 語の分類群の階級（表にあればそれ、なければ語尾（-idae = 科、-formes = 鳥の目）、それもなければ種リストの階級の列）"""
    t = norm_taxon(taxon)
    if t in LEVEL:
        return LEVEL[t]
    if t.endswith("idae"):
        return "family"
    if t.endswith("formes"):
        return "order"
    return RANKS.get((rank_hint or "").strip().lower()) or RANKS.get((rank_hint or "").strip())


def ancestors_of_taxon(taxon: str) -> list[str]:
    """分類群の上位を近い順に（mustela → [mustelidae, carnivora, mammalia]）。表になければ空。-formes の目は鳥類に"""
    out: list[str] = []
    t = norm_taxon(taxon)
    seen = {t}
    if t not in PARENT and t.endswith("formes"):
        return ["aves"]
    while t in PARENT:
        t = PARENT[t]
        if t in seen:
            break
        seen.add(t)
        out.append(t)
    return out


def _tokens(s: str) -> list[str]:
    s = (s or "").split("（")[0].split("(")[0].replace(",", " ").strip().lower()
    return [x for x in s.split() if x and x not in ("sp.", "sp", "spp.", "spp", "species", "family", "order", "class", "genus", "cf.", "aff.")]


def row_taxon(row: dict) -> tuple[str | None, str | None, list[str]]:
    """種リストの行の分類: (その行自身の分類群（1 語の行だけ。種は None）, 階級, 上位の分類群（近い順）)。
    学名（name_sci）を先に見て、なければ speciesnet 列。2 語以上なら種（属は最初の語）"""
    rank = row.get("rank") or ""
    for col in ("name_sci", "speciesnet"):
        toks = _tokens(row.get(col) or "")
        if not toks:
            continue
        if len(toks) >= 2:
            g = norm_taxon(toks[0])
            return None, "species", [g] + ancestors_of_taxon(g)
        t = norm_taxon(toks[0])
        return t, level_of(t, rank), ancestors_of_taxon(t)
    return None, RANKS.get(rank.strip().lower()) or RANKS.get(rank.strip()), []
