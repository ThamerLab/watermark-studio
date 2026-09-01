from pathlib import Path


def test_arabic_rtl_ui_and_no_external_assets():
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    assert 'lang="ar" dir="rtl"' in html
    assert "رفع الملف" in html
    assert "إنشاء PDF" in html
    assert "قائمة العلامات المائية" in html
    assert "تحميل نموذج CSV" in html
    assert "أضف نص العلامة المائية في كل صف. اسم المجلد اختياري." in html
    assert "قائمة المتدربين" not in html
    assert "http://" not in html and "https://" not in html


def test_compose_has_health_and_hardening():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert "/health" in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop" in compose
    assert "/var/run/docker.sock" not in compose


def test_installer_has_required_guards_and_flow():
    installer = Path("install.sh").read_text(encoding="utf-8")
    for token in ["pveversion", "pvesh get /cluster/nextid", "debian-13-standard", "--unprivileged 1", "nesting=1,keyctl=1", "docker-ce", "docker-compose-plugin", "/health", "Installation successful"]:
        assert token in installer
    assert "community-scripts.org" not in installer
    uninstall = Path("uninstall.sh").read_text(encoding="utf-8")
    assert 'Type DELETE $CTID' in uninstall
    assert "Watermark Studio managed container" in uninstall
