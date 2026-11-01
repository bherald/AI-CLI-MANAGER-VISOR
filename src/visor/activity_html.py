"""Shared, resource-free HTML formatting for native and browser Prism cards."""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import unquote, unquote_plus, urlsplit

from .activity_table import _border, _color, _integer

_TAGS = frozenset({"p", "br", "hr", "h1", "h2", "h3", "h4", "b", "strong", "i", "em",
                   "u", "ul", "ol", "li", "table", "thead", "tbody", "tr", "td", "th",
                   "pre", "code", "blockquote", "span", "div", "small", "sub", "sup", "a"})
_BLOCKED = frozenset({"script", "style", "iframe", "object", "embed", "svg", "math",
                      "template", "textarea", "noscript"})
_TABLE = frozenset({"table", "tr", "td", "th", "thead", "tbody"})
_DIGITS = re.compile(r"[0-9]{1,3}\Z")
_SPACE = re.compile(r"(?:[0-9]{1,2}px|0)(?: (?:[0-9]{1,2}px|0)){0,3}\Z")
_FONT = re.compile(r"([0-9]{1,2}(?:\.[0-9]{1,2})?)(px|pt)\Z")
_CREDENTIAL_PARAMETERS = frozenset({
    "token", "accesstoken", "refreshtoken", "idtoken", "authtoken", "apitoken",
    "csrftoken", "xsrftoken", "sessiontoken", "oauthtoken", "bearertoken",
    "password", "passwd", "pass", "secret", "clientsecret", "apikey", "key",
    "accesskey", "accesskeyid", "awsaccesskeyid", "credential", "credentials",
    "authorization", "auth", "bearer", "sessionid", "signature", "sig",
    "xamzcredential", "xamzsignature", "xamzsecuritytoken",
    "xgoogcredential", "xgoogsignature",
})


def safe_link(value):
    """Absolute HTTP(S) only; do not repair ambiguous or credential-bearing URLs."""
    if not isinstance(value, str) or not value:
        return None
    if any(ord(char) < 33 or ord(char) == 127 or char == "\\" for char in value):
        return None
    decoded = unquote(value)
    if any(ord(char) < 32 or ord(char) == 127 or char == "\\" for char in decoded):
        return None
    try:
        url = urlsplit(value)
        if (url.scheme.lower() not in {"http", "https"} or not url.hostname
                or url.username is not None or url.password is not None
                or "%" in url.netloc or url.port is not None and not 1 <= url.port <= 65535):
            return None
        # Inspect parameter names, including fragment-based login/route URLs.
        # Values and ordinary query parameters remain unchanged.
        for component in (url.query, url.fragment):
            for parameter in re.split(r"[&;?]", component):
                name, separator, _ = parameter.partition("=")
                if not separator:
                    continue
                name = unquote_plus(name)
                if "%" in name or re.sub(r"[^a-z0-9]", "", name.lower()) in _CREDENTIAL_PARAMETERS:
                    return None
    except ValueError:
        return None
    return value


def _rgb(value):
    _color(value, "HTML color")
    return value


def _bounded_integer(value, low, high):
    if not _DIGITS.fullmatch(value):
        raise ValueError("A bounded decimal is required")
    number = int(value)
    _integer(number, low, high, "HTML value")
    return str(number)


def _width(value):
    if value.endswith("%"):
        return _bounded_integer(value[:-1], 1, 100) + "%"
    return _bounded_integer(value.removesuffix("px"), 80, 800)


def _spacing(value):
    if not _SPACE.fullmatch(value):
        raise ValueError("Bounded pixel spacing is required")
    for item in value.split():
        _bounded_integer(item.removesuffix("px"), 0, 16)
    return value


def _style_value(key, value, tag):
    if key in {"color", "background-color"}:
        return _rgb(value)
    if key == "font-family" and re.fullmatch(r"[A-Za-z][A-Za-z ,_-]{0,99}", value):
        return value
    if key == "font-size":
        match = _FONT.fullmatch(value)
        if match and 1 <= float(match[1]) <= 28:
            return value  # Tiny authored spacer cells remain bounded; no hidden content or resources.
    if key == "font-weight" and value in {"normal", "bold", *map(str, range(100, 1000, 100))}:
        return value
    if key == "font-style" and value in {"normal", "italic", "oblique"}:
        return value
    if key == "text-align" and value in {"left", "center", "right", "justify"}:
        return value
    if key == "text-decoration" and value in {"none", "underline", "line-through"}:
        return value
    if key == "vertical-align" and value in {"top", "middle", "bottom", "baseline"}:
        return value
    if key in {"margin", "padding", "margin-top", "margin-bottom", "margin-left", "margin-right",
               "padding-top", "padding-bottom", "padding-left", "padding-right"}:
        return _spacing(value)
    if tag in _TABLE:
        if key == "width":
            width = _width(value)
            return width if width.endswith("%") else width + "px"
        if key == "border-collapse" and value in {"collapse", "separate"}:
            return value
        if key == "border-color":
            return _rgb(value)
        if key == "border-width":
            return _bounded_integer(value.removesuffix("px"), 0, 8) + "px"
        if key == "border-style":
            _border({"style": value})
            return value
        if key == "border":
            parts = value.split()
            if len(parts) == 3:
                width = int(_bounded_integer(parts[0].removesuffix("px"), 0, 8))
                _border({"width": width, "style": parts[1], "color": parts[2]})
                return f"{width}px {parts[1]} {parts[2]}"
    return None


class SafeHTML(HTMLParser):
    """Allow bounded typography/tables and deliberate links, never active resources."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.blocked = []
        self.anchors = []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCKED:
            self.blocked.append(tag)
            return
        if self.blocked or tag not in _TAGS:
            return
        attributes = dict(attrs)
        safe, styles = {}, {}
        if tag == "a":
            href = safe_link(attributes.get("href"))
            self.anchors.append(bool(href))
            if not href:
                return
            safe.update(href=href, target="_blank", rel="noopener noreferrer", referrerpolicy="no-referrer")
            if attributes.get("title"):
                safe["title"] = attributes["title"]
        for name, value in attributes.items():
            if not isinstance(value, str):
                continue
            try:
                if name == "style":
                    for rule in value.split(";"):
                        key, separator, val = rule.partition(":")
                        if not separator:
                            continue
                        key, val = key.strip().lower(), val.strip()
                        try:
                            validated = _style_value(key, val, tag)
                        except ValueError:
                            continue
                        if validated is not None:
                            styles[key] = validated
                elif tag in {"td", "th"} and name in {"rowspan", "colspan"}:
                    safe[name] = _bounded_integer(value, 1, 100)
                elif tag in _TABLE and name == "bgcolor":
                    safe[name] = _rgb(value)
                elif tag in {"table", "td", "th"} and name == "width":
                    safe[name] = _width(value)
                elif tag == "table" and name == "border":
                    safe[name] = _bounded_integer(value, 0, 8)
                elif tag == "table" and name == "bordercolor":
                    styles["border-color"] = _rgb(value)  # Qt uses CSS for this legacy attribute.
                elif tag == "table" and name in {"cellpadding", "cellspacing"}:
                    safe[name] = _bounded_integer(value, 0, 16)
            except ValueError:
                continue
        if styles:
            safe["style"] = ";".join(f"{key}:{value}" for key, value in styles.items())
        rendered = "".join(f' {key}="{html.escape(value, quote=True)}"' for key, value in safe.items())
        self.parts.append(f"<{tag}{rendered}>")

    def handle_endtag(self, tag):
        if self.blocked:
            if tag == self.blocked[-1]:
                self.blocked.pop()
        elif tag == "a":
            if self.anchors and self.anchors.pop():
                self.parts.append("</a>")
        elif tag in _TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.blocked:
            self.parts.append(html.escape(data))


def safe_html(value):
    parser = SafeHTML()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)
