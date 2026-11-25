# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Aspetto: palette, temi e costruttori di widget comuni."""
from __future__ import annotations

import customtkinter as ctk
from stillfind.core import system


# (chiaro, scuro)
PAL = {
    "bg": ("#f3f4f7", "#14161a"),
    "card": ("#ffffff", "#1d2026"),
    "line": ("#e2e4ea", "#2a2e37"),
    "text": ("#1c2030", "#e9ebf0"),
    "muted": ("#6a7283", "#8d95a5"),
    "accent": ("#4f46e5", "#6366f1"),
    "accent_hover": ("#4338ca", "#4f46e5"),
    "ghost_hover": ("#e8eaf1", "#262a33"),
    "field": ("#f3f4f7", "#14161a"),
    "hit_bg": ("#fde68a", "#6b5200"),
    "hit_fg": ("#1c2030", "#fff3c4"),
    "hit_now": ("#fb923c", "#c2410c"),   # l'occorrenza su cui ci si trova con ‹ ›
    "sel": ("#e0e7ff", "#2b3158"),
    "err": ("#b91c1c", "#f87171"),
}
THEMES = {"Sistema": "system", "Chiaro": "light", "Scuro": "dark"}


def dark() -> bool:
    return ctk.get_appearance_mode() == "Dark"


def col(name: str) -> str:
    """Colore per la modalità attuale (per i widget Tk che non accettano coppie)."""
    return PAL[name][1 if dark() else 0]


def font(size=13, bold=False) -> ctk.CTkFont:
    return ctk.CTkFont(size=size, weight="bold" if bold else "normal")


def ghost_button(master, text, command, width=0):
    return ctk.CTkButton(master, text=text, command=command, width=width, height=34, corner_radius=10,
                         fg_color="transparent", text_color=PAL["text"], hover_color=PAL["ghost_hover"],
                         border_width=1, border_color=PAL["line"], font=font(13))


def primary_button(master, text, command, width=0):
    return ctk.CTkButton(master, text=text, command=command, width=width, height=34, corner_radius=10,
                         fg_color=PAL["accent"], hover_color=PAL["accent_hover"], text_color="#ffffff",
                         font=font(13, True))


def card(master, **kw):
    return ctk.CTkFrame(master, corner_radius=14, fg_color=PAL["card"], border_width=1,
                        border_color=PAL["line"], **kw)


def action_icon(action):
    from PIL import Image, ImageDraw

    mask = Image.new("L", (64, 64))
    draw = ImageDraw.Draw(mask)
    if action == "open":
        draw.line([(29, 12), (10, 12), (10, 54), (52, 54), (52, 35)], fill=255, width=5)
        draw.line([(29, 35), (53, 11)], fill=255, width=5)
        draw.line([(35, 11), (53, 11), (53, 29)], fill=255, width=5)
    elif action == "folder":
        draw.line([(7, 51), (7, 13), (27, 13), (33, 20), (57, 20), (57, 51), (7, 51)],
                  fill=255, width=5, joint="curve")
        draw.line([(8, 27), (55, 27)], fill=255, width=4)
    elif action == "copy":
        draw.line([(15, 42), (10, 42), (10, 9), (40, 9), (40, 14)], fill=255, width=5)
        draw.rounded_rectangle((21, 19, 54, 55), radius=4, outline=255, width=5)
    elif action == "redact":
        draw.rounded_rectangle((9, 10, 55, 54), radius=5, outline=255, width=5)
        draw.line([(18, 22), (39, 22)], fill=255, width=4)
        draw.rectangle((17, 31, 47, 39), fill=255)
    else:
        raise ValueError(action)

    def tinted(color):
        icon = Image.new("RGBA", mask.size, color)
        icon.putalpha(mask)
        return icon

    light, dark_color = ("white", "white") if action == "open" else PAL["text"]
    return ctk.CTkImage(tinted(light), tinted(dark_color), size=(20, 20))
