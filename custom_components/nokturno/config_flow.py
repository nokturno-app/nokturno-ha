"""Nastavení integrace — účty zdrojů a předvolby přehrávání."""

from __future__ import annotations

import secrets

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .engine import STORAGE_OPTIONS
from .const import (
    SOURCE_TOGGLES,
    CONF_CZ_ENABLED,
    CONF_TERMS_ACCEPTED,
    CONF_TERMS_VERSION,
    TERMS_VERSION,
    CONF_DOWNLOAD_DIR,
    CONF_STATS_ENABLED,
    CONF_SYNC_CODE,
    CONF_SYNC_FAVOURITES,
    CONF_SYNC_HISTORY,
    CONF_SYNC_WATCHLIST,
    CONF_SYNC_CONCERTS,
    CONF_LASTFM_KEY,
    CONF_SYNC_KEY,
    CONF_SYNC_WATCHED,
    CONF_EXTERNAL_HOST,
    CONF_NOTIFY_TARGET,
    CONF_TRAKT_ID,
    CONF_TRAKT_SECRET,
    CONF_HIDE_3D,
    CONF_HIDE_DV,
    CONF_HIDE_HDR,
    CONF_HIDE_SD,
    CONF_KODI_ENTITY,
    CONF_LUNA_TOKEN,
    CONF_LUNA_URL,
    CONF_MAX_BITRATE,
    CONF_MULTI_PLAY,
    MULTI_PLAY_ASK,
    MULTI_PLAY_OPTIONS,
    CONF_PREF_LANG,
    CONF_PREF_SURROUND,
    CONF_SORT,
    CONF_STREAMUJ_PASS,
    CONF_ST_EMAIL,
    CONF_ST_PASS,
    CONF_FS_USER,
    CONF_FS_PASS,
    CONF_FS_PROVIDER,
    FS_PROVIDERS,
    CONF_PT_ENABLED,
    CONF_PT_EMAIL,
    CONF_PT_PASS,
    CONF_STREAMUJ_USER,
    CONF_TMDB_KEY,
    CONF_WS_PASS,
    CONF_HS_ENABLED,
    CONF_SUB_WARN_DAYS,
    CONF_WS_USER,
    DEFAULT_DOWNLOAD_DIR,
    DEFAULT_LUNA_URL,
    DEFAULT_SORT,
    DOMAIN,
    LANGS,
    SORT_ORDERS,
)

# vlastní úložiště (WebDAV), až tři — adresa, jméno, heslo, název; klíče drží jádro
STORAGE_KEYS = [key for slot in STORAGE_OPTIONS for key in slot]
# účty a klíče patří do `entry.data`, ne do options — od 2026-09-14 i Trakt
ACCOUNT_KEYS = [CONF_WS_USER, CONF_WS_PASS, CONF_STREAMUJ_USER, CONF_STREAMUJ_PASS, CONF_ST_EMAIL, CONF_ST_PASS,
                CONF_FS_USER, CONF_FS_PASS, CONF_PT_EMAIL, CONF_PT_PASS, CONF_LUNA_URL, CONF_LUNA_TOKEN, CONF_SYNC_KEY, CONF_SYNC_CODE, CONF_TMDB_KEY,
                CONF_TRAKT_ID, CONF_TRAKT_SECRET, CONF_LASTFM_KEY, *STORAGE_KEYS]
# ve formuláři skrytě — každé otevření Nastavení dřív ukázalo všech dvanáct hesel čitelně
SECRET_KEYS = frozenset({CONF_WS_PASS, CONF_STREAMUJ_PASS, CONF_ST_PASS, CONF_FS_PASS, CONF_PT_PASS, CONF_LUNA_TOKEN, CONF_TMDB_KEY, CONF_SYNC_KEY,
                         CONF_TRAKT_SECRET, CONF_LASTFM_KEY,
                         *(key for slot in STORAGE_OPTIONS for key in slot if key.endswith("_password"))})
ACCOUNT_DEFAULTS = {CONF_LUNA_URL: DEFAULT_LUNA_URL}


def _kod_skupiny(accounts: dict) -> str | None:
    """Srovná kód skupiny do tvaru `NKT-…` a vrátí chybu pro formulář, když nesedí.

    Prázdné pole znamená „HA do skupiny na dashboardu nechodí" — to je výchozí stav
    a žádná chyba. Kód se opisuje z obrazovky televize, takže se nehlídá jen tvar,
    ale rovnou se i normalizuje (malá písmena, chybějící pomlčky).
    """
    from .lib.syncbox import normalize_code, valid_code

    raw = (accounts.get(CONF_SYNC_CODE) or "").strip()
    if not raw:
        accounts[CONF_SYNC_CODE] = ""
        return None
    if not valid_code(raw):
        return "sync_code"
    accounts[CONF_SYNC_CODE] = normalize_code(raw)
    return None


# Nastavení je rozdělené do kroků. Formulář se čtyřiceti poli (i ve sbalitelných
# sekcích) byl nepřehledný, proto má Nastavení integrace menu: Přehrávání, Zdroje
# (podmenu po zdrojích), Vlastní úložiště (podmenu po slotech), Stahování,
# Synchronizace, Ostatní a Uložit. Každý krok je krátký formulář. Změny se drží
# v paměti flow (`_data`) a zapíšou se až volbou Uložit.
#
# Přidání integrace je průvodce: souhlas → přehrávání → vlastní úložiště → výběr volitelných
# zdrojů → formuláře jen vybraných zdrojů. Další úložiště, stahování a synchronizace jsou až v Nastavení.
# Uloženo zůstává naplocho jako dřív (`entry.data` účty, `entry.options` předvolby).
KROKY = {
    "prehravani": [CONF_KODI_ENTITY, CONF_MULTI_PLAY, CONF_PREF_LANG, CONF_PREF_SURROUND,
                   CONF_HIDE_SD, CONF_HIDE_3D, CONF_HIDE_DV, CONF_HIDE_HDR, CONF_MAX_BITRATE, CONF_SORT],
    "webshare": ["ws_enabled", CONF_WS_USER, CONF_WS_PASS, CONF_SUB_WARN_DAYS],
    "sosac": ["sc_enabled", CONF_STREAMUJ_USER, CONF_STREAMUJ_PASS],
    "hellspy": [CONF_HS_ENABLED],
    "sledujteto": ["st_enabled", CONF_ST_EMAIL, CONF_ST_PASS],
    "fastshare": ["fs_enabled", CONF_FS_PROVIDER, CONF_FS_USER, CONF_FS_PASS],
    "prehrajto": [CONF_PT_ENABLED, CONF_PT_EMAIL, CONF_PT_PASS],
    "cztor_zdroj": [CONF_CZ_ENABLED],
    "luna": ["luna_enabled", CONF_LUNA_URL, CONF_LUNA_TOKEN],
    **{f"uloziste{n}": [f"dav{n}_enabled", *slot] for n, slot in enumerate(STORAGE_OPTIONS, 1)},
    "stahovani": [CONF_DOWNLOAD_DIR, CONF_EXTERNAL_HOST, CONF_NOTIFY_TARGET],
    "synchronizace": [CONF_SYNC_KEY, CONF_SYNC_CODE, CONF_SYNC_WATCHED,
                      CONF_SYNC_FAVOURITES, CONF_SYNC_HISTORY, CONF_SYNC_WATCHLIST, CONF_SYNC_CONCERTS],
    "ostatni": [CONF_TMDB_KEY, CONF_LASTFM_KEY, CONF_TRAKT_ID, CONF_TRAKT_SECRET, CONF_STATS_ENABLED],
}
ZDROJE = ["webshare", "sosac", "hellspy", "sledujteto", "fastshare", "prehrajto", "cztor_zdroj", "luna"]
ULOZISTE = [f"uloziste{n}" for n in range(1, len(STORAGE_OPTIONS) + 1)]
# první pole kroku zdroje/úložiště je jeho přepínač „Používat …“
PREPINAC = {jmeno: KROKY[jmeno][0] for jmeno in ZDROJE + ULOZISTE}
JMENA_ZDROJU = {"webshare": "WebShare", "sosac": "Sosáč", "hellspy": "HellSpy", "sledujteto": "Sledujteto",
                "fastshare": "FastShare / Sdilej.cz", "prehrajto": "Přehraj.to", "cztor_zdroj": "CZtor", "luna": "Luna"}
MENU = ["prehravani", "uloziste", "zdroje", "stahovani", "synchronizace", "ostatni", "ulozit"]
# Zdroje třetích stran jsou volitelné: v průvodci nepředvybírá nic, zapne je jen uživatel sám.
VYCHOZI_ZDROJE: list[str] = []


def _pole(current: dict) -> dict:
    """Všechna pole jako {klíč: (marker, validátor)} k rozdělení do kroků."""
    out = dict(accounts_schema(current))
    out.update(preferences_schema(current).schema)
    return {marker.schema: (marker, validator) for marker, validator in out.items()}


def pole_kroku(jmeno: str) -> list[str]:
    """Klíče kroku. Pole, které by nebylo v žádném kroku, spadne do „ostatní“ –
    ať nový klíč nezmizí z nastavení jen proto, že se zapomnělo doplnit sem."""
    klice = list(KROKY[jmeno])
    if jmeno == "ostatni":
        jinde = {k for j, ks in KROKY.items() for k in ks}
        klice += [k for k in _pole({}) if k not in jinde]
    return klice


def schema_kroku(jmeno: str, current: dict, bez_prepinace: bool = False) -> vol.Schema:
    pole = _pole(current)
    klice = pole_kroku(jmeno)
    if bez_prepinace and jmeno in PREPINAC:
        klice = klice[1:]
    return vol.Schema({pole[k][0]: pole[k][1] for k in klice if k in pole})


def _zapnute(data: dict, kroky: list[str]) -> str:
    """Seznam zapnutých zdrojů / úložišť do popisu menu."""
    jmena = []
    for jmeno in kroky:
        if not data.get(PREPINAC[jmeno], jmeno != "cztor_zdroj"):
            continue
        if jmeno in ULOZISTE:
            n = jmeno[-1]
            if not data.get(f"dav{n}_url"):
                continue
            jmena.append(data.get(f"dav{n}_name") or n)
        else:
            jmena.append(JMENA_ZDROJU[jmeno])
    return ", ".join(jmena) or "—"


# Jméno jazyka v něm samém — v selectu se ukazuje místo holé zkratky.
LANG_LABELS = {"": "—", "CZ": "Čeština", "SK": "Slovenčina", "EN": "English", "HU": "Magyar"}


def _seznam(hodnota) -> list[str]:
    """Entity přehrávače vždy jako seznam. Do 6.6.0 se ukládal jeden řetězec a
    `EntitySelector(multiple=True)` by na něm spadl."""
    if not hodnota:
        return []
    return list(hodnota) if isinstance(hodnota, (list, tuple)) else [hodnota]


def _heslo():
    # `new-password`: prohlížeč jinak do polí vyplní uložené heslo k adrese HA
    # (na HA Home se tak do Sledujteto i FastShare uložilo heslo k SSH).
    return selector.TextSelector(selector.TextSelectorConfig(
        type=selector.TextSelectorType.PASSWORD, autocomplete="new-password"))


def accounts_schema(current: dict) -> dict:
    """Pole účtů pro oba formuláře — hesla a klíče jako skryté vstupy."""
    return {
        vol.Optional(key, default=current.get(key, ACCOUNT_DEFAULTS.get(key, ""))): _heslo() if key in SECRET_KEYS else str
        for key in ACCOUNT_KEYS
    }


def preferences_schema(data: dict) -> vol.Schema:
    """Předvolby přehrávání — stejné jako v Kodi doplňku."""
    return vol.Schema({
        # Víc přehrávačů naráz: služba `play` bez `entity_id` pustí titul na všech
        # vybraných. Uložená hodnota z dřívějška je jeden řetězec, proto `_seznam`.
        vol.Optional(CONF_KODI_ENTITY, default=_seznam(data.get(CONF_KODI_ENTITY))):
            selector.EntitySelector(
                selector.EntitySelectorConfig(domain="media_player", multiple=True)),
        # Co dělá Přehrát v kartě s víc nastavenými přehrávači. Dvě volby → HA je
        # vykreslí jako radio přepínače, stejně jako jazyk o kus níž.
        vol.Optional(CONF_MULTI_PLAY, default=data.get(CONF_MULTI_PLAY, MULTI_PLAY_ASK)):
            selector.SelectSelector(selector.SelectSelectorConfig(
                options=list(MULTI_PLAY_OPTIONS), translation_key="multi_play")),
        # Jazyky nesou popisek přímo (endonym), ne překlad: `translation_key` skládá
        # klíč z uložené hodnoty a hassfest povoluje jen `[a-z0-9-_]+`, kam „CZ"
        # ani „—" nepatří. Endonym je navíc srozumitelný v každém jazyce rozhraní.
        vol.Optional(CONF_PREF_LANG, default=data.get(CONF_PREF_LANG, "CZ")):
            selector.SelectSelector(selector.SelectSelectorConfig(
                options=[{"value": l or "—", "label": LANG_LABELS.get(l, l or "—")}
                         for l in LANGS])),
        vol.Optional(CONF_PREF_SURROUND, default=data.get(CONF_PREF_SURROUND, False)): bool,
        vol.Optional(CONF_HIDE_SD, default=data.get(CONF_HIDE_SD, False)): bool,
        vol.Optional(CONF_HIDE_3D, default=data.get(CONF_HIDE_3D, False)): bool,
        vol.Optional(CONF_HIDE_DV, default=data.get(CONF_HIDE_DV, False)): bool,
        vol.Optional(CONF_HIDE_HDR, default=data.get(CONF_HIDE_HDR, False)): bool,
        # NumberSelector místo vol.Range: HA by u nepovinného čísla kreslilo zaškrtávátko + posuvník
        vol.Optional(CONF_MAX_BITRATE, default=data.get(CONF_MAX_BITRATE, 0)):
            selector.NumberSelector(selector.NumberSelectorConfig(
                min=0, max=2000, step=0.5, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="Mb/s")),
        vol.Optional(CONF_SORT, default=data.get(CONF_SORT, DEFAULT_SORT)):
            selector.SelectSelector(selector.SelectSelectorConfig(
                options=SORT_ORDERS, translation_key="sort_streams")),
        vol.Optional(CONF_DOWNLOAD_DIR, default=data.get(CONF_DOWNLOAD_DIR, DEFAULT_DOWNLOAD_DIR)): str,
        vol.Optional(CONF_EXTERNAL_HOST, default=data.get(CONF_EXTERNAL_HOST, "")): str,
        vol.Optional(CONF_NOTIFY_TARGET, default=data.get(CONF_NOTIFY_TARGET, "")): str,
        # Trakt je účet → ACCOUNT_KEYS (entry.data), ne tady
        # HellSpy je veřejný, účet nepotřebuje — proto jen přepínač mezi předvolbami
        vol.Optional(CONF_FS_PROVIDER, default=data.get(CONF_FS_PROVIDER, "fastshare")):
            selector.SelectSelector(selector.SelectSelectorConfig(
                options=list(FS_PROVIDERS), translation_key="fs_provider")),
        vol.Optional(CONF_HS_ENABLED, default=data.get(CONF_HS_ENABLED, False)): bool,
        # „Používat …“ u zdrojů s účtem — vypnutý zdroj si údaje nechá (`SOURCE_TOGGLES`)
        **{vol.Optional(k, default=data.get(k, True)): bool for k in SOURCE_TOGGLES},
        # Přehraj.to funguje i bez účtu (první strana + překódovaný soubor), účet
        # je nepovinný a přidá stránkování i původní soubor — proto zapnuté ve výchozím
        # stavu jako HellSpy; e-mail a heslo jsou v ACCOUNT_KEYS (entry.data)
        vol.Optional(CONF_PT_ENABLED, default=data.get(CONF_PT_ENABLED, False)): bool,
        # CZtor účet do formuláře nepatří — zařízení se spáruje PINem v dalším kroku (`CztorPairing`)
        vol.Optional(CONF_CZ_ENABLED, default=data.get(CONF_CZ_ENABLED, False)): bool,
        # 0 = upozornění na konec předplatného WebShare vypnuté
        vol.Optional(CONF_SUB_WARN_DAYS, default=data.get(CONF_SUB_WARN_DAYS, 5)):
            selector.NumberSelector(selector.NumberSelectorConfig(
                min=0, max=14, step=1, mode=selector.NumberSelectorMode.BOX)),
        vol.Optional(CONF_STATS_ENABLED, default=data.get(CONF_STATS_ENABLED, True)): bool,
        # co se synchronizuje — platí pro Kodi v místní síti i pro skupinu na relayi
        vol.Optional(CONF_SYNC_WATCHED, default=data.get(CONF_SYNC_WATCHED, True)): bool,
        vol.Optional(CONF_SYNC_FAVOURITES, default=data.get(CONF_SYNC_FAVOURITES, True)): bool,
        vol.Optional(CONF_SYNC_HISTORY, default=data.get(CONF_SYNC_HISTORY, True)): bool,
        vol.Optional(CONF_SYNC_WATCHLIST, default=data.get(CONF_SYNC_WATCHLIST, True)): bool,
        vol.Optional(CONF_SYNC_CONCERTS, default=data.get(CONF_SYNC_CONCERTS, True)): bool,
    })


class CztorPairing:
    """Krok „CZtor": PIN z webu cztor.com/activate. Heslo účtu integrace nikdy nevidí,
    tokeny si drží úložiště jádra v `.storage/nokturno` (tam je čte i `Engine`).

    Zapnutý přepínač bez spárování otevře tenhle krok; formulář se uloží až po
    spárování, nebo když uživatel CZtor v kroku vypne."""

    _cz_pending: dict | None = None
    _cz_pin: dict | None = None

    def _cztor(self):
        from .lib.cztor_api import CztorApi
        from .lib.store import Store
        return CztorApi(Store(self.hass.config.path(f".storage/{DOMAIN}"),
                              cache_dir=self.hass.config.path(f".cache/{DOMAIN}")), device_name="Nokturno (Home Assistant)")

    async def _cztor_needs_pairing(self, user_input) -> bool:
        if not user_input.get(CONF_CZ_ENABLED):
            return False
        api = await self.hass.async_add_executor_job(self._cztor)
        return not await self.hass.async_add_executor_job(api.paired)

    async def async_step_cztor(self, user_input=None):
        from .lib.cztor_api import CztorError
        api = await self.hass.async_add_executor_job(self._cztor)
        errors = {}
        if user_input is not None:
            if not user_input.get("pair", True):
                self._cz_pending[CONF_CZ_ENABLED] = False
                return await self._cztor_finish()
            try:
                if self._cz_pin and await self.hass.async_add_executor_job(api.poll_pin, self._cz_pin["poll_token"]):
                    return await self._cztor_finish()
                errors["base"] = "cz_pending"
            except CztorError:
                self._cz_pin = None
                errors["base"] = "cz_failed"
        if not self._cz_pin:
            try:
                self._cz_pin = await self.hass.async_add_executor_job(api.start_pin)
            except CztorError:
                errors["base"] = "cz_network"
                self._cz_pin = {"pin": "—", "url": "https://cztor.com/activate", "poll_token": ""}
        schema = vol.Schema({vol.Optional("pair", default=True): bool})
        return self.async_show_form(step_id="cztor", data_schema=schema, errors=errors,
                                    description_placeholders={"url": self._cz_pin["url"], "pin": self._cz_pin["pin"]})


class Kroky(CztorPairing):
    """Společné kroky průvodce i Nastavení – jeden krok = jeden krátký formulář."""

    _data: dict | None = None
    _pruvodce = False   # průvodce vynechá přepínač zdroje, ten nastavil výběr zdrojů

    async def _krok(self, jmeno, user_input):
        errors = {}
        data = self._data
        if user_input is not None:
            vstup = dict(user_input)
            if vstup.get(CONF_PREF_LANG) == "—":
                vstup[CONF_PREF_LANG] = ""
            if jmeno == "synchronizace":
                chyba = _kod_skupiny(vstup)
                if chyba:
                    errors[CONF_SYNC_CODE] = chyba
            if not errors:
                self._data.update(vstup)
                return await self._po_kroku(jmeno)
            data = {**self._data, **vstup}
        return self.async_show_form(step_id=jmeno, errors=errors,
                                    data_schema=schema_kroku(jmeno, data, self._pruvodce))

    def _rozdel(self) -> tuple[dict, dict]:
        """`_data` na účty (`entry.data`) a předvolby (`entry.options`)."""
        predvolby = {m.schema for m in preferences_schema({}).schema}
        accounts = {k: self._data[k] for k in ACCOUNT_KEYS if k in self._data}
        prefs = {k: self._data[k] for k in predvolby if k in self._data}
        return accounts, prefs


for _jmeno in KROKY:
    setattr(Kroky, f"async_step_{_jmeno}",
            (lambda j: lambda self, user_input=None: self._krok(j, user_input))(_jmeno))


class NokturnoConfigFlow(Kroky, ConfigFlow, domain=DOMAIN):
    """Průvodce: souhlas → přehrávání → vlastní úložiště → výběr volitelných zdrojů → údaje vybraných zdrojů.
    Zbytek (úložiště, stahování, synchronizace…) je v Nastavení integrace."""

    VERSION = 1
    _pruvodce = True

    async def async_step_user(self, user_input=None):
        """Právní upozornění — první krok, bez potvrzení se instalace nedokončí."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors = {}
        if user_input is not None:
            if user_input.get(CONF_TERMS_ACCEPTED):
                # klíč pro synchronizaci s Kodi doplňkem — vzniká jednou, 128 bitů
                # (chrání neautentizované endpointy /sync a /files)
                self._data = {CONF_SYNC_KEY: secrets.token_hex(16)}
                self._fronta = []
                return await self.async_step_prehravani()
            errors["base"] = "terms_required"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_TERMS_ACCEPTED, default=False): bool}),
            errors=errors,
        )

    async def async_step_vyber_zdroju(self, user_input=None):
        if user_input is not None:
            vybrane = user_input.get("zdroje") or []
            for jmeno in ZDROJE:
                self._data[PREPINAC[jmeno]] = jmeno in vybrane
            # zdroje bez dalších údajů (HellSpy, CZtor) formulář nemají
            self._fronta = [j for j in ZDROJE if j in vybrane and len(KROKY[j]) > 1]
            return await self._dalsi()
        schema = vol.Schema({vol.Optional("zdroje", default=VYCHOZI_ZDROJE): selector.SelectSelector(
            selector.SelectSelectorConfig(options=ZDROJE, multiple=True, translation_key="zdroje",
                                          mode=selector.SelectSelectorMode.LIST))})
        return self.async_show_form(step_id="vyber_zdroju", data_schema=schema)

    async def _po_kroku(self, jmeno):
        if jmeno == "prehravani":
            return await self.async_step_uloziste1()
        if jmeno == "uloziste1":
            self._data["dav1_enabled"] = True
            return await self.async_step_vyber_zdroju()
        return await self._dalsi()

    async def _dalsi(self):
        if self._fronta:
            return await getattr(self, f"async_step_{self._fronta.pop(0)}")()
        accounts, prefs = self._rozdel()
        # potvrzení právního upozornění patří do entry.data, ať se při reconfiguraci
        # nemusí ptát znovu na stejnou verzi textu
        accounts[CONF_TERMS_ACCEPTED] = True
        accounts[CONF_TERMS_VERSION] = TERMS_VERSION
        self._cz_pending, self._cz_accounts = prefs, accounts
        if await self._cztor_needs_pairing(prefs):
            return await self.async_step_cztor()
        return await self._cztor_finish()

    async def async_step_reauth(self, entry_data):
        """WebShare odmítl přihlášení — HA ukáže „vyžaduje opravu" a tenhle krok."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            from .lib.webshare_api import WebshareApi, WebshareApiError, WebshareError
            api = WebshareApi(user_input[CONF_WS_USER], user_input[CONF_WS_PASS])
            try:
                await self.hass.async_add_executor_job(api.login)
            except WebshareApiError:
                errors["base"] = "ws_auth"
            except WebshareError:
                errors["base"] = "ws_network"
            if not errors:
                return self.async_update_reload_and_abort(entry, data={**entry.data, **user_input})
        schema = vol.Schema({
            vol.Required(CONF_WS_USER, default=entry.data.get(CONF_WS_USER, "")): str,
            vol.Required(CONF_WS_PASS): _heslo(),
        })
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)

    async def _cztor_finish(self):
        return self.async_create_entry(title="Nokturno", data=self._cz_accounts, options=self._cz_pending)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return NokturnoOptionsFlow()


class NokturnoOptionsFlow(Kroky, OptionsFlow):
    """Nastavení jako menu. Změny se drží v `_data` a zapíšou se až volbou Uložit
    (účty do `entry.data`, předvolby do `entry.options`)."""

    def _aktualni(self) -> dict:
        if self._data is None:
            self._data = {**self.config_entry.data, **self.config_entry.options}
        return self._data

    async def async_step_init(self, user_input=None):
        data = self._aktualni()
        return self.async_show_menu(step_id="init", menu_options=MENU, description_placeholders={
            "zdroje": _zapnute(data, ZDROJE), "uloziste": _zapnute(data, ULOZISTE)})

    async def async_step_zdroje(self, user_input=None):
        return self.async_show_menu(step_id="zdroje", menu_options=[*ZDROJE, "init"],
                                    description_placeholders={"zdroje": _zapnute(self._aktualni(), ZDROJE)})

    async def async_step_uloziste(self, user_input=None):
        return self.async_show_menu(step_id="uloziste", menu_options=[*ULOZISTE, "init"],
                                    description_placeholders={"uloziste": _zapnute(self._aktualni(), ULOZISTE)})

    async def _krok(self, jmeno, user_input):
        self._aktualni()
        return await super()._krok(jmeno, user_input)

    async def _po_kroku(self, jmeno):
        if jmeno in ZDROJE:
            return await self.async_step_zdroje()
        if jmeno in ULOZISTE:
            return await self.async_step_uloziste()
        return await self.async_step_init()

    async def async_step_ulozit(self, user_input=None):
        self._aktualni()
        accounts, prefs = self._rozdel()
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**self.config_entry.data, **accounts})
        self._cz_pending = {**self.config_entry.options, **prefs}
        if await self._cztor_needs_pairing(self._cz_pending):
            return await self.async_step_cztor()
        return await self._cztor_finish()

    async def _cztor_finish(self):
        return self.async_create_entry(title="", data=self._cz_pending)
