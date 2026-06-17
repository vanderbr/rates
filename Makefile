# SPDX-License-Identifier: Apache-2.0

export PYTHONDONTWRITEBYTECODE := 1

.PHONY: test audit artifacts update update-actuarial update-backfill

test:
	python3 -B -m unittest scripts.tests.test_archive_irs_pdf_source scripts.tests.test_audit_contract scripts.tests.test_report_historical_irs_source_leads scripts.tests.test_import_2015_vbt scripts.tests.test_update_actuarial_tables scripts.tests.test_update_afr_rates scripts.tests.test_update_annual_gift_exclusion scripts.tests.test_update_gst_exemption scripts.tests.test_update_noncitizen_spouse_gift_exclusion scripts.tests.test_update_section_7520_rates scripts.tests.test_update_unified_estate_gift_tax_exemption scripts.tests.test_update_market_rates scripts.tests.test_repository_layout

audit:
	python3 scripts/artifact_contract.py
	python3 scripts/audit_contract.py
	protoc --proto_path=proto --descriptor_set_out=/tmp/rates.desc proto/rates/v1/rates.proto
	buf lint
	buf generate --template buf.gen.yaml
	$(MAKE) test

artifacts:
	python3 scripts/artifact_contract.py

update:
	python3 scripts/update_section_7520_rates.py --write
	python3 scripts/update_afr_rates.py --write --archive-sources
	python3 scripts/update_annual_gift_exclusion.py --write
	python3 scripts/update_gst_exemption.py --write
	python3 scripts/update_noncitizen_spouse_gift_exclusion.py --write
	python3 scripts/update_unified_estate_gift_tax_exemption.py --write
	python3 scripts/update_market_rates.py --write
	python3 scripts/artifact_contract.py

update-actuarial:
	python3 scripts/update_actuarial_tables.py --write
	python3 scripts/artifact_contract.py

update-backfill:
	python3 scripts/update_section_7520_rates.py --backfill --write
	python3 scripts/update_afr_rates.py --backfill --write
	python3 scripts/update_annual_gift_exclusion.py --backfill --write
	python3 scripts/update_gst_exemption.py --backfill --write
	python3 scripts/update_noncitizen_spouse_gift_exclusion.py --backfill --write
	python3 scripts/update_unified_estate_gift_tax_exemption.py --backfill --write
	python3 scripts/update_market_rates.py --backfill --write
	python3 scripts/artifact_contract.py
