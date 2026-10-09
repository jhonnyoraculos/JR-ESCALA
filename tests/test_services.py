import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from web import db, jr_rotas, services


class ServiceCrudTests(unittest.TestCase):
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
        self.motorista = services.add_colaborador("Motorista", "Motorista")
        self.ajudante = services.add_colaborador("Ajudante", "Ajudante")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_carregamento_create_update_delete_and_duplicate_protection(self):
        carregamento_id = services.salvar_carregamento(
            "2026-10-01",
            "R.10 - DIVINÓPOLIS",
            None,
            self.motorista,
            self.ajudante,
            "ROTA 1 DIA (BATE E VOLTA)",
            data_saida="2026-10-02",
        )
        services.criar_bloqueios_para_carregamento(
            carregamento_id,
            "2026-10-01",
            [self.motorista, self.ajudante],
            "ROTA 1 DIA (BATE E VOLTA)",
        )

        with self.assertRaisesRegex(ValueError, "Já existe"):
            services.salvar_carregamento(
                "2026-10-01",
                "R.10 - DIVINÓPOLIS",
                None,
                None,
                None,
                "0",
            )

        services.atualizar_carregamento(
            carregamento_id,
            "2026-10-02",
            "2026-10-03",
            "R.10 - DIVINÓPOLIS",
            "ABC-1D23",
            self.motorista,
            self.ajudante,
            "ROTA 1 DIA (BATE E VOLTA)",
            "Atualizado",
        )
        updated = services.obter_carregamento(carregamento_id)
        self.assertEqual(updated["data"], "2026-10-02")
        self.assertEqual(updated["placa"], "ABC-1D23")
        self.assertEqual(updated["observacao_extra"], "Atualizado")
        self.assertEqual(updated["revisado"], 1)

        with self.assertRaisesRegex(ValueError, "pessoas diferentes"):
            services.atualizar_carregamento(
                carregamento_id,
                "2026-10-02",
                "2026-10-03",
                "R.10 - DIVINÓPOLIS",
                None,
                self.motorista,
                self.motorista,
                "0",
            )

        services.remover_carregamento_completo(carregamento_id)
        self.assertIsNone(services.obter_carregamento(carregamento_id))
        with db.get_connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM bloqueios WHERE carregamento_id = ?;",
                (carregamento_id,),
            )
            self.assertEqual(cursor.fetchone()[0], 0)

        with self.assertRaisesRegex(ValueError, "não encontrado"):
            services.atualizar_carregamento(
                999999,
                "2026-10-02",
                "2026-10-03",
                "R.999 - INEXISTENTE",
                None,
                None,
                None,
                "0",
            )

    def test_log_starts_on_departure_tracks_progress_and_allows_finalization(self):
        futuro_id = services.salvar_carregamento(
            "2026-10-08",
            "R.20 - FUTURA",
            "FUT-1A23",
            self.motorista,
            self.ajudante,
            "ROTA 3 DIAS",
            observacao_extra="Carga frágil",
            observacao_cor="#FFF59D",
            data_saida="2026-10-12",
            revisado=True,
        )
        andamento_id = services.salvar_carregamento(
            "2026-10-08",
            "R.21 - EM VIAGEM",
            "AND-4B56",
            self.motorista,
            self.ajudante,
            "ROTA 2 DIAS",
            data_saida="2026-10-09",
        )
        services.criar_bloqueios_para_carregamento(
            andamento_id,
            "2026-10-09",
            [self.motorista, self.ajudante],
            "ROTA 2 DIAS",
        )

        with mock.patch.object(services, "_hoje_local", return_value=date(2026, 10, 9)):
            em_andamento = services.consultar_log_carregamentos(
                {"status": "Em andamento"}
            )
            agendados = services.consultar_log_carregamentos({"status": "Agendados"})
            filtrado_por_saida = services.consultar_log_carregamentos(
                {
                    "status": "Todos",
                    "data_inicio": "2026-10-12",
                    "data_fim": "2026-10-12",
                }
            )

        self.assertEqual([item["id"] for item in em_andamento], [andamento_id])
        self.assertEqual(em_andamento[0]["progresso_percentual"], 50)
        self.assertEqual([item["id"] for item in agendados], [futuro_id])
        self.assertEqual([item["id"] for item in filtrado_por_saida], [futuro_id])
        self.assertEqual(agendados[0]["observacao_extra"], "Carga frágil")
        self.assertEqual(agendados[0]["observacao_cor"], "#FFF59D")
        self.assertTrue(agendados[0]["revisado"])

        services.finalizar_carregamento(andamento_id)
        with mock.patch.object(services, "_hoje_local", return_value=date(2026, 10, 9)):
            finalizados = services.consultar_log_carregamentos(
                {"status": "Finalizados"}
            )
        finalizado = next(item for item in finalizados if item["id"] == andamento_id)
        self.assertTrue(finalizado["finalizado_em"])
        with db.get_connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM bloqueios WHERE carregamento_id = ?;",
                (andamento_id,),
            )
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_sync_requested_collaborators_preserves_fretados_and_is_idempotent(self):
        services.atualizar_colaborador(
            self.motorista,
            "aldemir luiz da silva",
            "Ajudante",
            "Cadastro existente",
            None,
            True,
        )
        duplicate_id = services.add_colaborador("ALDÉMIR  LUIZ DA SILVA", "Ajudante")
        services.desativar_colaborador(duplicate_id)
        antigo_id = services.add_colaborador("COLABORADOR ANTIGO", "Motorista")
        fretado_id = services.add_colaborador("FRETADO (TESTE)", "Motorista")
        carregamento_id = services.salvar_carregamento(
            "2026-10-01",
            "R.998 - TESTE MIGRAÇÃO",
            None,
            duplicate_id,
            antigo_id,
            "0",
        )

        self.assertTrue(services.sincronizar_colaboradores_20261001())
        self.assertFalse(services.sincronizar_colaboradores_20261001())

        colaboradores = services.listar_colaboradores()
        self.assertEqual(
            len(colaboradores),
            len(services.COLABORADORES_20261001) + 1,
        )
        self.assertEqual(
            len({item["nome"] for item in colaboradores}),
            len(colaboradores),
        )
        aldemir = next(
            item for item in colaboradores if item["nome"] == "ALDEMIR LUIZ DA SILVA"
        )
        self.assertEqual(aldemir["id"], self.motorista)
        self.assertEqual(aldemir["funcao"], "Motorista")
        self.assertEqual(aldemir["ativo"], 1)
        self.assertIsNone(services.obter_colaborador_por_id(duplicate_id))
        self.assertIsNone(services.obter_colaborador_por_id(antigo_id))
        self.assertIsNotNone(services.obter_colaborador_por_id(fretado_id))

        carregamento = services.obter_carregamento(carregamento_id)
        self.assertEqual(carregamento["motorista_id"], self.motorista)
        self.assertIsNone(carregamento["ajudante_id"])

    def test_fretado_uses_exclusive_truck_automatically(self):
        fretado_id = services.add_colaborador("FRETADO (TESTE)", "Motorista")
        outro_fretado_id = services.add_colaborador("FRETADO (OUTRO)", "Motorista")
        exclusivo_id = services.add_caminhao("FRT-1A23", "Fretado", "Exclusivo")
        geral_id = services.add_caminhao("GER-4B56", "Geral", "")

        services.vincular_caminhao_fretado(fretado_id, exclusivo_id)
        vinculo = services.obter_caminhao_fretado(fretado_id)
        self.assertEqual(vinculo["placa"], "FRT-1A23")
        self.assertEqual(
            [item["id"] for item in services.listar_caminhoes_gerais()],
            [geral_id],
        )

        carregamento_id = services.salvar_carregamento(
            "2026-10-01",
            "R.997 - FRETADO",
            "GER-4B56",
            fretado_id,
            None,
            "0",
        )
        self.assertEqual(
            services.obter_carregamento(carregamento_id)["placa"],
            "FRT-1A23",
        )

        pendente_id = services.salvar_carregamento(
            "2026-10-02",
            "R.30 - ITAGUARA",
            None,
            None,
            None,
            "ROTA 1 DIA (BATE E VOLTA)",
        )
        services.atualizar_carregamento(
            pendente_id,
            "2026-10-02",
            "2026-10-05",
            "R.30 - ITAGUARA",
            None,
            fretado_id,
            self.ajudante,
            "ROTA 1 DIA (BATE E VOLTA)",
        )
        atualizado = services.obter_carregamento(pendente_id)
        self.assertEqual(atualizado["placa"], "FRT-1A23")
        self.assertEqual(atualizado["motorista_id"], fretado_id)
        self.assertEqual(atualizado["ajudante_id"], self.ajudante)
        self.assertEqual(atualizado["revisado"], 1)

        with self.assertRaisesRegex(ValueError, "exclusivo"):
            services.salvar_carregamento(
                "2026-10-01",
                "R.996 - MOTORISTA",
                "FRT-1A23",
                self.motorista,
                None,
                "0",
            )
        with self.assertRaisesRegex(ValueError, "outro fretado"):
            services.vincular_caminhao_fretado(outro_fretado_id, exclusivo_id)

        services.desvincular_caminhao_fretado(fretado_id)
        self.assertIsNone(services.obter_caminhao_fretado(fretado_id))
        self.assertEqual(
            {item["id"] for item in services.listar_caminhoes_gerais()},
            {exclusivo_id, geral_id},
        )

    def test_add_fretado_with_optional_exclusive_truck(self):
        fretado_id, caminhao_id = services.adicionar_fretado(
            "Transportes Silva",
            "frt-9z99",
            "Mercedes 709",
            "Uso exclusivo",
        )

        fretado = services.obter_colaborador_por_id(fretado_id)
        self.assertEqual(fretado["nome"], "FRETADO (TRANSPORTES SILVA)")
        self.assertEqual(fretado["funcao"], "Motorista")
        vinculo = services.obter_caminhao_fretado(fretado_id)
        self.assertEqual(vinculo["id"], caminhao_id)
        self.assertEqual(vinculo["placa"], "FRT-9Z99")
        self.assertNotIn(
            caminhao_id,
            [item["id"] for item in services.listar_caminhoes_gerais()],
        )

        sem_caminhao_id, sem_caminhao = services.adicionar_fretado("Novo parceiro")
        self.assertIsNone(sem_caminhao)
        self.assertIsNone(services.obter_caminhao_fretado(sem_caminhao_id))

        with self.assertRaisesRegex(ValueError, "já está cadastrado"):
            services.adicionar_fretado("transportes  silva")

        caminhao_existente_id = services.add_caminhao(
            "GER-7C89", "Caminhão da frota", "Disponível"
        )
        fretado_existente_id, caminhao_movido_id = services.adicionar_fretado(
            "Parceiro com veículo existente",
            caminhao_existente_id=caminhao_existente_id,
        )
        self.assertEqual(caminhao_movido_id, caminhao_existente_id)
        self.assertEqual(
            services.obter_caminhao_fretado(fretado_existente_id)["id"],
            caminhao_existente_id,
        )
        self.assertNotIn(
            caminhao_existente_id,
            [item["id"] for item in services.listar_caminhoes_gerais()],
        )

        with self.assertRaisesRegex(ValueError, "não os dois"):
            services.adicionar_fretado(
                "Parceiro inválido",
                "NOV-1A23",
                caminhao_existente_id=caminhao_existente_id,
            )

    def test_oficina_edit_persists_changed_date_and_delete(self):
        oficina_id = services.salvar_oficina(
            "2026-10-01",
            self.motorista,
            "ABC-1D23",
            "Revisão",
            data_saida="2026-10-02",
        )
        services.editar_oficina(
            oficina_id,
            "2026-10-03",
            self.motorista,
            "ABC-1D23",
            "Pneus",
            "Concluído",
            "2026-10-04",
        )
        self.assertEqual(services.listar_oficinas("2026-10-01"), [])
        updated = services.obter_oficina(oficina_id)
        self.assertEqual(updated["data"], "2026-10-03")
        self.assertEqual(updated["observacao"], "Pneus")
        services.excluir_oficina(oficina_id)
        self.assertIsNone(services.obter_oficina(oficina_id))

    def test_folga_interval_is_validated_and_blocks_every_day(self):
        folga_id = services.salvar_folga(
            "2026-10-05", self.motorista, "2026-10-07"
        )
        indisponiveis = services.verificar_disponibilidade("2026-10-06")
        self.assertIn(self.motorista, indisponiveis["motoristas"])

        services.editar_folga(
            folga_id,
            "2026-10-08",
            "2026-10-09",
            None,
            self.motorista,
            None,
            None,
            None,
        )
        self.assertNotIn(
            self.motorista,
            services.verificar_disponibilidade("2026-10-06")["motoristas"],
        )
        self.assertIn(
            self.motorista,
            services.verificar_disponibilidade("2026-10-09")["motoristas"],
        )
        with self.assertRaisesRegex(ValueError, "Data inicial"):
            services.salvar_folga(
                "2026-10-10", self.ajudante, "2026-10-09"
            )
        services.remover_folga(folga_id)
        self.assertEqual(services.listar_folgas("2026-10-08"), [])

    def test_escala_cd_edit_persists_changed_date_and_delete(self):
        escala_id = services.adicionar_escala_cd(
            "2026-10-10", self.motorista, self.ajudante, "Separação"
        )
        services.editar_escala_cd(
            escala_id,
            "2026-10-11",
            self.motorista,
            self.ajudante,
            "Expedição",
        )
        self.assertEqual(services.listar_escala_cd("2026-10-10"), [])
        updated = services.obter_escala_cd(escala_id)
        self.assertEqual(updated["data"], "2026-10-11")
        self.assertEqual(updated["observacao"], "Expedição")
        with self.assertRaisesRegex(ValueError, "pessoas diferentes"):
            services.editar_escala_cd(
                escala_id,
                "2026-10-11",
                self.motorista,
                self.motorista,
                "Inválida",
            )
        services.excluir_escala_cd(escala_id)
        self.assertIsNone(services.obter_escala_cd(escala_id))

    def test_auto_fill_ignores_route_inserted_by_another_rerun(self):
        rota = {
            "rota": "R.30",
            "destino": "ITAGUARA",
            "observacao": "",
        }
        with (
            mock.patch.object(services, "listar_rotas_para_data", return_value=[rota]),
            mock.patch.object(services, "listar_rotas_suprimidas", return_value=set()),
            mock.patch.object(
                services,
                "carregamento_existe_para_rota",
                side_effect=[False, True],
            ),
            mock.patch.object(
                services,
                "salvar_carregamento",
                side_effect=ValueError("Já existe um carregamento desta rota nesta data."),
            ),
        ):
            self.assertEqual(
                services.preencher_carregamentos_automaticos("2026-10-07"), 0
            )

    def test_holiday_check_uses_selected_routes_and_actual_departure(self):
        services.adicionar_rota_semana(
            "quarta", "R.71", "VARGINHA", "ROTA 1 DIA (BATE E VOLTA)"
        )
        services.adicionar_rota_semana(
            "quinta", "R.600", "PEDRO LEOPOLDO", "ROTA 1 DIA (BATE E VOLTA)"
        )
        services.salvar_carregamento(
            "2026-10-07",
            "R.71 - VARGINHA",
            None,
            None,
            None,
            "ROTA 3 DIAS",
            data_saida="2026-10-08",
        )

        with mock.patch.object(
            jr_rotas, "verify_route_holidays", return_value=([], [])
        ) as verify:
            services.verificar_feriados_rotas_semanais(
                date(2026, 10, 7), date(2026, 10, 8)
            )

        rotas, referencia = verify.call_args.args
        self.assertEqual(referencia, date(2026, 10, 7))
        self.assertEqual(len(rotas), 1)
        self.assertEqual(rotas[0]["rota"], "R.71")
        self.assertEqual(rotas[0]["data_saida"], "2026-10-08")
        self.assertEqual(rotas[0]["observacao"], "ROTA 3 DIAS")

    def test_atestado_crud_blocks_the_complete_inclusive_period(self):
        atestado_id = services.adicionar_atestado(
            self.motorista,
            "2026-10-07",
            3,
            "Repouso médico",
        )
        registro = next(
            item for item in services.listar_atestados() if item["id"] == atestado_id
        )
        self.assertEqual(registro["data_fim"], "2026-10-09")
        self.assertEqual(registro["dias_ausencia"], 3)

        for data_bloqueada in ("2026-10-07", "2026-10-08", "2026-10-09"):
            indisponiveis = services.verificar_disponibilidade(data_bloqueada)
            self.assertIn(self.motorista, indisponiveis["motoristas"])
            self.assertIn(self.motorista, indisponiveis["ajudantes"])
        self.assertNotIn(
            self.motorista,
            services.verificar_disponibilidade("2026-10-10")["motoristas"],
        )

        with self.assertRaisesRegex(ValueError, "Já existe um atestado"):
            services.adicionar_atestado(self.motorista, "2026-10-09", 2)

        services.atualizar_atestado(
            atestado_id,
            self.motorista,
            "2026-10-08",
            2,
            "Período corrigido",
        )
        atualizado = next(
            item for item in services.listar_atestados() if item["id"] == atestado_id
        )
        self.assertEqual(atualizado["data_inicio"], "2026-10-08")
        self.assertEqual(atualizado["data_fim"], "2026-10-09")
        self.assertEqual(atualizado["observacao"], "Período corrigido")

        services.remover_atestado(atestado_id)
        self.assertEqual(services.listar_atestados(), [])
        self.assertNotIn(
            self.motorista,
            services.verificar_disponibilidade("2026-10-08")["motoristas"],
        )

    def test_supporting_cadastros_crud_and_protected_route(self):
        caminhao_id = services.add_caminhao("abc-1d23", "Modelo A", "Novo")
        services.editar_caminhao(
            caminhao_id, "def-4g56", "Modelo B", "Atualizado", False
        )
        caminhao = next(
            item
            for item in services.listar_caminhoes(ativos_only=False)
            if item["id"] == caminhao_id
        )
        self.assertEqual(caminhao["placa"], "DEF-4G56")
        self.assertEqual(caminhao["ativo"], 0)

        rota_id = services.adicionar_rota_semana(
            "segunda", "R.99", "Destino", "Observação"
        )
        services.editar_rota_semana(
            rota_id, "terça", "R.99", "Destino novo", "Atualizada"
        )
        rota = services.listar_rotas_semanais("terça")[0]
        self.assertEqual(rota["destino"], "Destino novo")

        quinta_id = services.adicionar_rota_semana(
            "quinta", "R.98", "Carga automática", ""
        )
        with mock.patch.object(jr_rotas, "source_database_url", return_value=None):
            self.assertEqual(
                services.preencher_carregamentos_automaticos("2026-10-01"), 1
            )
            self.assertEqual(
                services.preencher_carregamentos_automaticos("2026-10-01"), 0
            )
        self.assertEqual(len(services.listar_carregamentos("2026-10-01")), 1)

        with db.get_connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO rotas_semanais
                    (dia_semana, rota, destino, observacao, origem, origem_id)
                VALUES ('quarta', 'R.100', 'Oficial', '', 'jr_rotas', '2:R.100');
                """
            )
            connection.commit()
        with self.assertRaisesRegex(ValueError, "JR Rotas"):
            services.remover_rota_semana(
                services.listar_rotas_semanais("quarta")[0]["id"]
            )

        ferias_id = services.adicionar_ferias(
            self.ajudante, "2026-11-01", "2026-11-05", "Descanso"
        )
        services.atualizar_ferias(
            ferias_id, self.ajudante, "2026-11-02", "2026-11-06", "Atualizada"
        )
        ferias = next(item for item in services.listar_ferias() if item["id"] == ferias_id)
        self.assertEqual(ferias["data_inicio"], "2026-11-02")

        services.atualizar_colaborador(
            self.motorista, "Motorista Atualizado", "Motorista", "OK", None, False
        )
        colaborador = services.obter_colaborador_por_id(self.motorista)
        self.assertEqual(colaborador["nome"], "Motorista Atualizado")
        self.assertEqual(colaborador["ativo"], 0)

        services.remover_ferias(ferias_id)
        services.remover_rota_semana(quinta_id)
        services.remover_rota_semana(rota_id)
        services.remover_caminhao(caminhao_id)
        services.excluir_colaborador(self.motorista)
        self.assertIsNone(services.obter_colaborador_por_id(self.motorista))


if __name__ == "__main__":
    unittest.main()
