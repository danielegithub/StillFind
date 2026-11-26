# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Aspetto: palette, temi e costruttori di widget comuni."""
from __future__ import annotations

import customtkinter as ctk

# (chiaro, scuro)
PAL = {
    "bg": ("#e9eef3", "#111a24"),
    "card": ("#ffffff", "#1c2733"),
    "line": ("#b9c5d1", "#445163"),
    "text": ("#18293b", "#e2e9f0"),
    "muted": ("#536477", "#a6b5c5"),
    "accent": ("#175d9c", "#2467a5"),
    "accent_hover": ("#104775", "#1b568c"),
    "focus": ("#175d9c", "#8ac4ff"),
    "ghost_hover": ("#dce6ef", "#2b3c4e"),
    "field": ("#f5f8fb", "#15202c"),
    "hit_bg": ("#f6e3a3", "#574619"),
    "hit_fg": ("#392b08", "#fff0bc"),
    "hit_now": ("#efb961", "#87551b"),
    "sel": ("#dceafb", "#233e5a"),
    "err": ("#a52828", "#ff9696"),
    "ready": ("#23673e", "#8bd6a7"),
    "pending": ("#815b12", "#e8c17a"),
}
THEME_IDS = ("system", "light", "dark")


def configure_theme():
    """Default comuni a spigolo vivo, anche per i sotto-widget creati da CTk.

    I valori espliciti nelle singole viste saranno migrati nei rispettivi passi
    del piano; nessuna modifica alle classi di CustomTkinter a runtime.
    """
    theme = ctk.ThemeManager.theme
    for values in theme.values():
        for key in ("corner_radius", "button_corner_radius"):
            if key in values:
                values[key] = 0
        if "border_width" in values:
            values["border_width"] = 1
        if "text_color" in values:
            values["text_color"] = PAL["text"]
        if "text_color_disabled" in values:
            values["text_color_disabled"] = PAL["muted"]
        if "border_color" in values:
            values["border_color"] = PAL["line"]
    for name in ("CTk", "CTkToplevel"):
        theme[name]["fg_color"] = PAL["bg"]
    theme["CTkFrame"].update(fg_color=PAL["card"], top_fg_color=PAL["card"])
    theme["CTkLabel"]["border_width"] = 0
    theme["CTkButton"].update(fg_color=PAL["accent"], hover_color=PAL["accent_hover"],
                              border_color=PAL["accent"], text_color=("#ffffff", "#ffffff"))
    for name in ("CTkEntry", "CTkTextbox", "CTkComboBox"):
        theme[name]["fg_color"] = PAL["field"]
    theme["CTkEntry"]["placeholder_text_color"] = PAL["muted"]
    theme["CTkTextbox"].update(scrollbar_button_color=PAL["muted"], scrollbar_button_hover_color=PAL["focus"])
    theme["CTkCheckBox"].update(fg_color=PAL["accent"], hover_color=PAL["accent_hover"], checkmark_color="white")
    theme["CTkSwitch"].update(fg_color=PAL["line"], progress_color=PAL["accent"],
                              button_color=("#ffffff", "#ffffff"), button_hover_color=PAL["sel"], button_length=14)
    theme["CTkRadioButton"].update(fg_color=PAL["accent"], hover_color=PAL["accent_hover"],
                                   border_width_checked=5, border_width_unchecked=1)
    theme["CTkProgressBar"].update(fg_color=PAL["field"], progress_color=PAL["accent"])
    theme["CTkSlider"].update(fg_color=PAL["line"], progress_color=PAL["accent"],
                              button_color=PAL["accent"], button_hover_color=PAL["accent_hover"], button_length=12)
    for name in ("CTkOptionMenu", "CTkComboBox"):
        theme[name].update(button_color=PAL["line"], button_hover_color=PAL["ghost_hover"])
    theme["CTkOptionMenu"]["fg_color"] = PAL["field"]
    theme["CTkScrollbar"].update(border_spacing=0, button_color=PAL["muted"], button_hover_color=PAL["focus"])
    theme["CTkSegmentedButton"].update(fg_color=PAL["line"], selected_color=PAL["sel"],
                                       selected_hover_color=PAL["ghost_hover"], unselected_color=PAL["field"],
                                       unselected_hover_color=PAL["ghost_hover"])
    theme["CTkScrollableFrame"]["label_fg_color"] = PAL["card"]
    theme["DropdownMenu"].update(fg_color=PAL["card"], hover_color=PAL["sel"])


configure_theme()


def dark() -> bool:
    return ctk.get_appearance_mode() == "Dark"


def col(name: str) -> str:
    """Colore per la modalità attuale (per i widget Tk che non accettano coppie)."""
    return PAL[name][1 if dark() else 0]


def font(size=13, bold=False) -> ctk.CTkFont:
    return ctk.CTkFont(size=size, weight="bold" if bold else "normal")


def fit_width(text: str, width: int, bold: bool = False) -> int:
    """Larghezza di un pulsante: quella richiesta, o di più se il testo (tradotto) non ci starebbe."""
    try:
        return max(width, font(13, bold).measure(text) + 40) if width else width
    except Exception:
        return width


def ghost_button(master, text, command, width=0):
    width = fit_width(text, width)
    return ctk.CTkButton(master, text=text, command=command, width=width, height=34, corner_radius=0,
                         fg_color="transparent", text_color=PAL["text"], hover_color=PAL["ghost_hover"],
                         border_width=1, border_color=PAL["line"], font=font(13))


def primary_button(master, text, command, width=0):
    width = fit_width(text, width, True)
    return ctk.CTkButton(master, text=text, command=command, width=width, height=34, corner_radius=0,
                         fg_color=PAL["accent"], hover_color=PAL["accent_hover"], text_color="#ffffff",
                         text_color_disabled=("#c9d8e6", "#9fb4c9"),
                         border_width=1, border_color=PAL["accent"], font=font(13, True))


def card(master, **kw):
    return ctk.CTkFrame(master, corner_radius=0, fg_color=PAL["card"], border_width=1,
                        border_color=PAL["line"], **kw)


def icon_mask(action):
    """Pittogrammi ortogonali: solo rettangoli e segmenti orizzontali/verticali."""
    from PIL import Image, ImageDraw

    mask = Image.new("L", (64, 64))
    draw = ImageDraw.Draw(mask)
    if action == "open":
        draw.line([(26, 12), (10, 12), (10, 54), (52, 54), (52, 40)], fill=255, width=5)
        draw.line([(28, 40), (28, 24), (54, 24)], fill=255, width=5)
        draw.line([(44, 14), (44, 18), (50, 18), (50, 30), (44, 30), (44, 34)], fill=255, width=5)
    elif action == "folder":
        draw.line([(7, 51), (7, 13), (29, 13), (29, 20), (57, 20), (57, 51), (7, 51)], fill=255, width=5)
        draw.line([(8, 27), (55, 27)], fill=255, width=4)
    elif action == "copy":
        draw.line([(15, 42), (10, 42), (10, 9), (40, 9), (40, 14)], fill=255, width=5)
        draw.rectangle((21, 19, 54, 55), outline=255, width=5)
    elif action == "redact":
        draw.rectangle((9, 10, 55, 54), outline=255, width=5)
        draw.line([(18, 22), (39, 22)], fill=255, width=4)
        draw.rectangle((17, 31, 47, 39), fill=255)
    elif action in ("lock", "unlock"):
        draw.rectangle((13, 28, 51, 55), outline=255, width=5)
        if action == "lock":
            draw.line([(22, 28), (22, 10), (42, 10), (42, 28)], fill=255, width=5)
        else:
            draw.line([(22, 28), (22, 10), (44, 10), (44, 18)], fill=255, width=5)
        draw.rectangle((29, 37, 35, 46), fill=255)
    elif action in ("embedding", "embedding_ready", "embedding_pending", "embedding_no_text", "embedding_disabled"):
        draw.rectangle((8, 8, 26, 26), outline=255, width=4)
        draw.rectangle((38, 8, 56, 26), outline=255, width=4)
        draw.rectangle((8, 38, 26, 56), outline=255, width=4)
        draw.line([(26, 17), (38, 17)], fill=255, width=4)
        draw.line([(17, 26), (17, 38)], fill=255, width=4)
        if action in ("embedding", "embedding_ready"):
            draw.rectangle((38, 38, 56, 56), fill=255)
            draw.line([(26, 47), (38, 47)], fill=255, width=4)
            draw.line([(47, 26), (47, 38)], fill=255, width=4)
        elif action == "embedding_pending":
            for x in (36, 44, 52):
                draw.rectangle((x, 44, x + 4, 49), fill=255)
        elif action == "embedding_no_text":
            draw.rectangle((44, 35, 49, 47), fill=255)
            draw.rectangle((44, 52, 49, 56), fill=255)
        else:
            draw.rectangle((36, 44, 56, 49), fill=255)
    else:
        raise ValueError(action)
    return mask


_ICON_COLORS = {"embedding_ready": "ready", "embedding_pending": "pending",
                "embedding_no_text": "err", "embedding_disabled": "muted"}


def icon_pil(action, dark_mode, size=None):
    """Il pittogramma come immagine PIL colorata per il tema chiaro o scuro (per i widget Tk senza coppie di colori)."""
    from PIL import Image

    mask = icon_mask(action)
    light, dark_color = ("white", "white") if action == "open" else PAL[_ICON_COLORS.get(action, "text")]
    icon = Image.new("RGBA", mask.size, dark_color if dark_mode else light)
    icon.putalpha(mask)
    return icon.resize((size, size), Image.LANCZOS) if size else icon


def action_icon(action):
    return ctk.CTkImage(icon_pil(action, False), icon_pil(action, True), size=(20, 20))
