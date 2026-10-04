from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ui import i18n


class I18nParityTests(unittest.TestCase):
    """Regression: every key RU defines must also exist in EN.

    EXTRA_TRANSLATIONS_RU starts as a copy of EXTRA_TRANSLATIONS_EN and then
    .update()s overrides/additions. A key added only to the RU block renders as
    its raw key string in the English UI (tr() falls back to the key). The
    draw_outpaint_* anchor/prompt/quality keys regressed this way.
    """

    def test_ru_has_no_keys_missing_from_en(self) -> None:
        en = set(i18n.EXTRA_TRANSLATIONS_EN.keys())
        ru = set(i18n.EXTRA_TRANSLATIONS_RU.keys())
        missing = ru - en
        self.assertEqual(
            missing,
            set(),
            f"RU defines keys absent from EN (English UI would show raw keys): {sorted(missing)}",
        )

    def test_outpaint_keys_present_in_en(self) -> None:
        required = [
            "draw_outpaint_anchor_h",
            "draw_outpaint_anchor_v",
            "draw_outpaint_anchor_left",
            "draw_outpaint_anchor_center",
            "draw_outpaint_anchor_right",
            "draw_outpaint_anchor_top",
            "draw_outpaint_anchor_bottom",
            "draw_outpaint_prompt_section",
            "draw_outpaint_prompt_label",
            "draw_outpaint_negative_label",
            "draw_outpaint_quality_title",
            "draw_outpaint_steps_label",
            "draw_outpaint_cfg_label",
            "draw_outpaint_seed_label",
            "draw_outpaint_sd_unavailable_title",
            "draw_outpaint_sd_unavailable_body",
        ]
        for key in required:
            self.assertIn(key, i18n.EXTRA_TRANSLATIONS_EN)


class QmlEnglishTests(unittest.TestCase):
    """Every Russian qsTr() text of the main window has an English entry.

    A missing entry silently stays Russian in the English interface: the whole
    AI page did so until the 2.0 audit (2026-10-01).
    """

    def test_every_qml_text_is_translated(self) -> None:
        import json
        import re
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "ui" / "quick"
        english = json.loads((root / "i18n" / "en.json").read_text(encoding="utf-8"))
        missing = []
        for qml in sorted((root / "qml").glob("*.qml")):
            for match in re.finditer(r'qsTr\("((?:[^"\\]|\\.)*)"\)', qml.read_text(encoding="utf-8")):
                # the key at run time is the string with its escapes resolved
                text = match.group(1).replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
                if re.search("[А-Яа-яЁё]", text) and text not in english:
                    missing.append(f"{qml.name}: {text}")
        self.assertEqual(missing, [])

    def test_every_whats_new_item_is_translated(self) -> None:
        # The list comes from Python, so the qsTr scan above does not see it.
        import json
        from pathlib import Path
        from application.support import WHATS_NEW
        english = json.loads((Path(__file__).resolve().parents[1] / "ui" / "quick" / "i18n" / "en.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual([item for item in WHATS_NEW if item not in english], [])


if __name__ == "__main__":
    unittest.main()
