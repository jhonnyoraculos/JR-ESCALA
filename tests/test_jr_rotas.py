import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from web import db, jr_rotas


class TripDurationTests(unittest.TestCase):
    def test_reads_multi_day_observation(self):
        self.assertEqual(jr_rotas.trip_duration_days("ROTA 3 DIAS"), 3)
        self.assertEqual(jr_rotas.trip_duration_days("ROTA 1 DIA (BATE E VOLTA)"), 1)
        self.assertEqual(jr_rotas.trip_duration_days("observação livre"), 1)

    def test_extracts_only_day_level_operational_notes(self):
        notes = jr_rotas._matrix_day_notes(
            {
                "0": [
                    "Observação antes das rotas",
                    "ITAÚNA (R.40)",
                    "Mateus Leme",
                    "COLETA ESPECIAL",
                    "Retorno após as 16h",
                ]
            }
        )
        self.assertEqual(
            notes[0],
            "Observação antes das rotas · COLETA ESPECIAL · Retorno após as 16h",
        )

    def test_holiday_on_last_day_of_trip_is_reported(self):
        routes = [
            {
                "id": 1,
                "origem_id": "0:R.40",
                "dia_semana": "segunda",
                "rota": "R.40",
                "destino": "Itaúna",
                "observacao": "ROTA 3 DIAS",
                "cidades_json": json.dumps(
                    [
                        {
                            "city_original": "Itaúna",
                            "municipality_name": "Itaúna",
                            "state": "MG",
                            "ibge_code": "3133808",
                        }
                    ]
                ),
            }
        ]
        municipal = {
            "3133808": (
                {
                    "date": date(2026, 9, 30),
                    "name": "Feriado local",
                    "type": "Municipal",
                },
            )
        }
        with (
            mock.patch.object(jr_rotas, "_general_holidays", return_value=()),
            mock.patch.object(jr_rotas, "_municipal_dataset", return_value=municipal),
            mock.patch.object(jr_rotas, "source_database_url", return_value=None),
        ):
            alerts, warnings = jr_rotas.verify_route_holidays(routes, date(2026, 9, 28))

        self.assertFalse(warnings)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].holiday_date, date(2026, 9, 30))
        self.assertEqual(alerts[0].return_date, date(2026, 9, 30))

    def test_source_uses_official_template_and_main_route_name(self):
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        cursor = mock.MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [
            {
                "weekday": 3,
                "code": "R.600",
                "destination": "PEDRO LEOPOLDO",
                "position": 7,
                "cities": [],
            }
        ]
        cursor.fetchone.return_value = {"value": "{}"}

        with mock.patch.object(jr_rotas, "_source_connection", return_value=connection):
            routes = jr_rotas._fetch_source_routes("postgresql://read-only")

        source_query = cursor.execute.call_args_list[0].args[0]
        self.assertIn("route_weekday_template", source_query)
        self.assertIn("r.name AS destination", source_query)
        self.assertEqual(routes[0]["dia_semana"], "quinta")
        self.assertEqual(routes[0]["destino"], "PEDRO LEOPOLDO")


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.patches = (
            mock.patch.object(db, "DB_PATH", self.db_path),
            mock.patch.object(db, "USE_POSTGRES", False),
        )
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        db.init_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def route(code: str, destination: str, source_id: str) -> dict:
        item = {
            "origem_id": source_id,
            "dia_semana": "segunda",
            "rota": code,
            "destino": destination,
            "observacao": "",
            "ordem": 0,
            "cidades": [],
        }
        item["origem_hash"] = jr_rotas._route_hash(item)
        return item

    def test_snapshot_updates_incrementally_without_duplicates(self):
        first = self.route("R.40", "Itaúna", "0:R.40")
        self.assertEqual(jr_rotas._apply_snapshot([first]), (1, 0, 0, 0))
        self.assertEqual(jr_rotas._apply_snapshot([first]), (0, 0, 0, 0))

        changed = self.route("R.40", "Itaúna e região", "0:R.40")
        second = self.route("R.41", "Pará de Minas", "0:R.41")
        self.assertEqual(jr_rotas._apply_snapshot([changed, second]), (1, 1, 0, 0))
        self.assertEqual(jr_rotas._apply_snapshot([second]), (0, 0, 1, 0))

        with db.get_connection(dict_rows=True) as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT rota, destino, origem FROM rotas_semanais;")
            rows = [dict(row) for row in cursor.fetchall()]
        connection.close()
        self.assertEqual(
            rows, [{"rota": "R.41", "destino": "Pará de Minas", "origem": "jr_rotas"}]
        )

    def test_snapshot_removes_all_old_local_routes(self):
        with db.get_connection() as connection:
            cursor = connection.cursor()
            cursor.executemany(
                """
                INSERT INTO rotas_semanais
                    (dia_semana, rota, destino, observacao, origem)
                VALUES (?, ?, ?, '', 'local');
                """,
                [
                    ("segunda", "ANTIGA 1", "Destino antigo"),
                    ("sabado", "ANTIGA 2", "Destino de sábado"),
                ],
            )
            connection.commit()
        connection.close()

        official = self.route("R.40", "Itaúna", "0:R.40")
        self.assertEqual(jr_rotas._apply_snapshot([official]), (1, 0, 2, 0))

        with db.get_connection(dict_rows=True) as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT rota, origem FROM rotas_semanais ORDER BY rota;")
            rows = [dict(row) for row in cursor.fetchall()]
        connection.close()
        self.assertEqual(rows, [{"rota": "R.40", "origem": "jr_rotas"}])

    def test_snapshot_cleans_current_legacy_loads_and_official_duplicates(self):
        with db.get_connection() as connection:
            cursor = connection.cursor()
            cursor.executemany(
                """
                INSERT INTO carregamentos
                    (data, data_saida, rota, observacao, revisado)
                VALUES (?, ?, ?, '0', 0);
                """,
                [
                    ("2026-10-01", "2026-10-02", "40 - ITAÚNA"),
                    ("2026-10-01", "2026-10-02", "R.40 - ITAÚNA"),
                    ("2026-10-01", "2026-10-02", "R.40 - ITAÚNA"),
                    ("2026-09-30", "2026-10-01", "40 - ITAÚNA"),
                ],
            )
            connection.commit()
        connection.close()

        official = self.route("R.40", "Itaúna", "3:R.40")
        official["dia_semana"] = "quinta"
        official["origem_hash"] = jr_rotas._route_hash(official)
        self.assertEqual(jr_rotas._apply_snapshot([official]), (1, 0, 0, 2))

        with db.get_connection(dict_rows=True) as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT data, rota FROM carregamentos ORDER BY data, id;")
            rows = [dict(row) for row in cursor.fetchall()]
        connection.close()
        self.assertEqual(
            rows,
            [
                {"data": "2026-09-30", "rota": "40 - ITAÚNA"},
                {"data": "2026-10-01", "rota": "R.40 - ITAÚNA"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
