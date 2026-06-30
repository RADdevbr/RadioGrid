#!/usr/bin/env python3
"""Testes leves do RadioGrid (stdlib unittest, offline, sem servidor HTTP)."""

import os
import sys
import json
import shutil
import tempfile
import unittest

import radiogrid
from macos_bridge import normalize_name
from radiogrid import check_name_conflict


class TestNormalizeName(unittest.TestCase):
    def test_removes_accents_and_uppercases(self):
        self.assertEqual(normalize_name("José da Silva"), "JOSE DA SILVA")
        self.assertEqual(normalize_name("Leandro Sales Araújo"), "LEANDRO SALES ARAUJO")

    def test_collapses_whitespace(self):
        self.assertEqual(normalize_name("  joao   silva  "), "JOAO SILVA")

    def test_empty_returns_unknown(self):
        self.assertEqual(normalize_name(""), "DESCONHECIDO")
        self.assertEqual(normalize_name("   "), "DESCONHECIDO")


class TestNameConflict(unittest.TestCase):
    def _imgs(self, names):
        return [{"name_detected": n} for n in names]

    def test_consistent(self):
        res = check_name_conflict(self._imgs(["JOAO SILVA", "JOAO SILVA"]))
        self.assertFalse(res["conflict"])
        self.assertEqual(res["name"], "JOAO SILVA")

    def test_divergent(self):
        res = check_name_conflict(self._imgs(["JOAO SILVA", "JOSE SILVA", "JOAO SILVA"]))
        self.assertTrue(res["conflict"])
        self.assertEqual(res["most_common"], "JOAO SILVA")
        self.assertIn("JOSE SILVA", res["names_found"])

    def test_no_ocr(self):
        res = check_name_conflict(self._imgs(["DESCONHECIDO", "DESCONHECIDO"]))
        self.assertFalse(res["conflict"])
        self.assertEqual(res["reason"], "no_ocr")


class TestPipeline(unittest.TestCase):
    """Adicionar 4 imagens dispara a geração de painel e zera a fila."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Redireciona caminhos de persistência para o tmp.
        self._orig = (radiogrid.CONFIG_PATH, radiogrid.STATE_PATH)
        radiogrid.CONFIG_PATH = os.path.join(self.tmp, "config.json")
        radiogrid.STATE_PATH = os.path.join(self.tmp, "state.json")
        self.out = os.path.join(self.tmp, "output")
        os.makedirs(self.out, exist_ok=True)

        self.app = radiogrid.RadioGrid()
        self.app.config["output_folder"] = self.out
        self.app.config["watch_folders"] = [self.tmp]

        # Cria 4 imagens dummy (conteúdo qualquer — stub apenas copia).
        self.imgs = []
        for i in range(4):
            p = os.path.join(self.tmp, f"PACIENTE_TESTE_{i}.png")
            with open(p, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 16)
            self.imgs.append(p)

    def tearDown(self):
        radiogrid.CONFIG_PATH, radiogrid.STATE_PATH = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_four_images_generate_panel(self):
        patient = "PACIENTE TESTE"
        for p in self.imgs:
            self.app._add_image(patient, p, patient, "test")

        # Fila zerada após geração automática.
        q = self.app.state["queues"][patient]
        self.assertEqual(q["count"], 0)
        self.assertEqual(q["panel_count"], 1)
        self.assertEqual(q["notified_at_4"], False)

        # Painel registrado e arquivo criado.
        self.assertEqual(len(self.app.state["panels"]), 1)
        panel = self.app.state["panels"][0]
        self.assertEqual(panel["patient"], patient)
        self.assertTrue(os.path.isfile(panel["path"]))
        self.assertEqual(len(panel["sources"]), 4)

    def test_partial_queue_persists(self):
        patient = "PACIENTE TESTE"
        for p in self.imgs[:2]:
            self.app._add_image(patient, p, patient, "test")
        self.assertEqual(self.app.state["queues"][patient]["count"], 2)
        self.assertEqual(len(self.app.state["panels"]), 0)

        # state.json foi persistido com a fila parcial.
        with open(radiogrid.STATE_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["queues"][patient]["count"], 2)

    def test_manual_generate_with_fewer(self):
        patient = "PACIENTE TESTE"
        for p in self.imgs[:2]:
            self.app._add_image(patient, p, patient, "test")
        panel = self.app.generate_panel(patient)
        self.assertIsNotNone(panel)
        self.assertEqual(self.app.state["queues"][patient]["count"], 0)

    def test_watcher_uses_default_queue_without_ocr(self):
        # on_new_image não usa mais OCR: imagens vão para a fila padrão.
        self.app.on_new_image(self.imgs[0])
        self.assertIn(radiogrid.DEFAULT_PATIENT, self.app.state["queues"])
        self.assertEqual(
            self.app.state["queues"][radiogrid.DEFAULT_PATIENT]["count"], 1
        )

    def test_queue_reorder(self):
        patient = "PAC ORDEM"
        for p in self.imgs[:3]:
            self.app._add_image(patient, p, patient, "test")
        nova = list(reversed(self.imgs[:3]))
        res = self.app.queue_reorder(patient, nova)
        self.assertTrue(res["ok"])
        got = [i["path"] for i in self.app.state["queues"][patient]["images"]]
        self.assertEqual(got, nova)

    def test_edit_panel_restores_and_regenerates(self):
        patient = "PACIENTE TESTE"
        for p in self.imgs:  # 4 imagens -> gera painel_1 e zera a fila
            self.app._add_image(patient, p, patient, "test")
        self.assertEqual(self.app.state["queues"][patient]["count"], 0)
        panel1 = self.app.state["panels"][0]

        res = self.app.edit_panel(panel1["path"])
        self.assertTrue(res["ok"])
        self.assertEqual(self.app.state["queues"][patient]["count"], 4)

        panel2 = self.app.generate_panel(patient)
        self.assertIsNotNone(panel2)
        self.assertEqual(panel2["panel_number"], 2)
        self.assertEqual(len(self.app.state["panels"]), 2)

    def test_edit_panel_missing_sources(self):
        res = self.app.edit_panel("/nao/existe/painel.png")
        self.assertFalse(res["ok"])


class TestImport(unittest.TestCase):
    """Importação manual: por caminho local e por upload base64."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = (radiogrid.CONFIG_PATH, radiogrid.STATE_PATH, radiogrid.IMPORT_DIR)
        radiogrid.CONFIG_PATH = os.path.join(self.tmp, "config.json")
        radiogrid.STATE_PATH = os.path.join(self.tmp, "state.json")
        radiogrid.IMPORT_DIR = os.path.join(self.tmp, "imports")

        self.app = radiogrid.RadioGrid()
        self.app.config["output_folder"] = os.path.join(self.tmp, "output")
        self.app.config["watch_folders"] = [self.tmp]

        self.src = os.path.join(self.tmp, "origem.png")
        with open(self.src, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    def tearDown(self):
        radiogrid.CONFIG_PATH, radiogrid.STATE_PATH, radiogrid.IMPORT_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_import_by_path_copies_and_queues(self):
        res = self.app.import_images("Maria Souza", paths=[self.src])
        self.assertTrue(res["ok"])
        self.assertEqual(res["imported"], 1)
        q = self.app.state["queues"]["MARIA SOUZA"]
        self.assertEqual(q["count"], 1)
        dest = q["images"][0]["path"]
        self.assertTrue(dest.startswith(radiogrid.IMPORT_DIR))
        self.assertTrue(os.path.isfile(dest))  # cópia, não o original
        self.assertTrue(os.path.isfile(self.src))
        self.assertTrue(self.app.path_allowed(dest))  # thumbnail/Finder liberados

    def test_import_by_upload_base64(self):
        import base64
        with open(self.src, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        res = self.app.import_images(
            "Maria Souza",
            files=[{"name": "upload.png", "data_base64": "data:image/png;base64," + data}],
        )
        self.assertEqual(res["imported"], 1)
        self.assertEqual(self.app.state["queues"]["MARIA SOUZA"]["count"], 1)

    def test_import_without_patient_uses_default_queue(self):
        res = self.app.import_images("", paths=[self.src])
        self.assertTrue(res["ok"])
        self.assertEqual(res["imported"], 1)
        self.assertIn(radiogrid.DEFAULT_PATIENT, self.app.state["queues"])
        self.assertEqual(
            self.app.state["queues"][radiogrid.DEFAULT_PATIENT]["count"], 1
        )

    def test_import_rejects_non_image(self):
        txt = os.path.join(self.tmp, "nota.txt")
        with open(txt, "w") as f:
            f.write("x")
        res = self.app.import_images("Maria Souza", paths=[txt])
        self.assertEqual(res["imported"], 0)
        self.assertTrue(res["errors"])


class TestWebRISPanelGeometry(unittest.TestCase):
    """O painel composto (app nativo) deve ser WebRIS-safe: 640×500 na largura
    nativa da coluna do laudo — o WebRIS não reescala (sem distorção) e a altura
    (500px) deixa a imagem e o bloco de assinatura na mesma página. (Medido no
    PDF: 525px ainda coube; 560px empurrava a assinatura.)
    """

    def setUp(self):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            self.skipTest("Pillow não instalado — composição real indisponível")
        import macos_bridge
        self.macos_bridge = macos_bridge
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(getattr(self, "tmp", ""), ignore_errors=True)

    def _make_img(self, name, size, color):
        from PIL import Image
        path = os.path.join(self.tmp, name)
        Image.new("RGB", size, color).save(path, "PNG")
        return path

    def _compose(self, sources):
        out = os.path.join(self.tmp, "panel.png")
        ok = self.macos_bridge.compose_panel(sources, out)
        self.assertTrue(ok)
        from PIL import Image
        with Image.open(out) as im:
            return im.size  # (w, h)

    def _assert_webris_safe(self, size):
        w, h = size
        # Largura natural até 640; acima disso o WebRIS reduz p/ 640.
        rendered_h = h if w <= 640 else h * 640 / w
        self.assertLessEqual(rendered_h, 500 + 0.5,
                             f"altura renderizada {rendered_h:.0f}px > 500px")
        self.assertLessEqual(w, 640 + 0.5, f"largura {w}px > 640px (estoura a coluna)")

    def test_four_images_panel_is_640x500(self):
        srcs = [self._make_img(f"i{i}.png", (400, 400), (i * 40, 0, 0)) for i in range(4)]
        self.assertEqual(self._compose(srcs), (640, 500))

    def test_panel_is_webris_safe_for_1_to_4(self):
        srcs = [self._make_img(f"j{i}.png", (300 + i * 50, 500), (0, i * 40, 0)) for i in range(4)]
        for n in range(1, 5):
            with self.subTest(n=n):
                self._assert_webris_safe(self._compose(srcs[:n]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
