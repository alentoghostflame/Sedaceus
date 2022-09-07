from enum import Enum, IntEnum, unique


__all__ = (
    "ApplicationCommandType",
)


@unique
class ApplicationCommandType(IntEnum):
    CHAT_INPUT = 1
    USER = 2
    MESSAGE = 3


@unique
class Locale(Enum):
    da = "da"
    """Danish | Dansk"""
    de = "de"
    """German | Deutsch"""
    en_GB = "en-GB"
    """English, UK | English, UK"""
    en_US = "en-US"
    """English, US | English, US"""
    es_ES = "es-ES"
    """Spanish | Español"""
    fr = "fr"
    """French | Français"""
    hr = "hr"
    """Croatian | Hrvatski"""
    it = "it"
    """Italian | Italiano"""
    lt = "lt"
    """Lithuanian | Lietuviškai"""
    hu = "hu"
    """Hungarian | Magyar"""
    nl = "nl"
    """Dutch | Nederlands"""
    no = "no"
    """Norwegian | Norsk"""
    pl = "pl"
    """Polish | Polski"""
    pt_BR = "pt-BR"
    """Portuguese, Brazilian | Português do Brasil"""
    ro = "ro"
    """Romanian, Romania | Română"""
    fi = "fi"
    """Finnish | Suomi"""
    sv_SE = "sv-SE"
    """Swedish | Svenska"""
    vi = "vi"
    """Vietnamese | Tiếng Việt"""
    tr = "tr"
    """Turkish | Türkçe"""
    cs = "cs"
    """Czech | Čeština"""
    el = "el"
    """Greek | Ελληνικά"""
    bg = "bg"
    """Bulgarian | български"""
    ru = "ru"
    """Russian | Pусский"""
    uk = "uk"
    """Ukrainian | Українська"""
    hi = "hi"
    """Hindi | हिन्दी"""
    th = "th"
    """Thai	| ไทย"""
    zh_CN = "zh-CN"
    """Chinese, China | 中文"""
    ja = "ja"
    """Japanese | 日本語"""
    zh_TW = "zh-TW"
    """Chinese, Taiwan | 繁體中文"""
    ko = "ko"
    """Korean | 한국어"""

    def __str__(self):
        return self.value
