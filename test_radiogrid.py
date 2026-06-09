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


if __name__ == "__main__":
    unittest.main(verbosity=2)
