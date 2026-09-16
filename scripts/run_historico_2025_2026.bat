@echo off
echo === Descarga historica mes a mes: enero 2025 - mayo 2026 ===

python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2025-01-01 00:00" --end-date "2025-01-31 23:59:59"
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2025-02-01 00:00" --end-date "2025-02-28 23:59:59"
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2025-03-01 00:00" --end-date "2025-03-31 23:59:59"
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2025-04-01 00:00" --end-date "2025-04-30 23:59:59"
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2025-05-01 00:00" --end-date "2025-05-31 23:59:59"
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2025-06-01 00:00" --end-date "2025-06-30 23:59:59"
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2025-07-01 00:00" --end-date "2025-07-31 23:59:59"
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2025-08-01 00:00" --end-date "2025-08-31 23:59:59"
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2025-09-01 00:00" --end-date "2025-09-30 23:59:59"
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2025-10-01 00:00" --end-date "2025-10-31 23:59:59"
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2025-11-01 00:00" --end-date "2025-11-30 23:59:59"
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2025-12-01 00:00" --end-date "2025-12-31 23:59:59"
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2026-01-01 00:00" --end-date "2026-01-31 23:59:59"
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2026-02-01 00:00" --end-date "2026-02-28 23:59:59"
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2026-03-01 00:00" --end-date "2026-03-31 23:59:59"
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2026-04-01 00:00" --end-date "2026-04-30 23:59:59"
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2026-05-01 00:00" --end-date "2026-05-27 23:59:59"

echo === Descarga completada ===
pause