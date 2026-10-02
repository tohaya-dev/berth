from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "ops" / "windows-wsl.ps1").read_text()


class WindowsWslLauncher(unittest.TestCase):
    def test_launcher_is_detached_from_openssh_job(self):
        self.assertIn("Invoke-CimMethod -ClassName Win32_Process -MethodName Create", SOURCE)
        self.assertNotIn("Start-Process -FilePath wsl.exe", SOURCE)

    def test_launcher_records_and_validates_the_created_process(self):
        self.assertIn("Get-Process -Id ([int]$created.ProcessId)", SOURCE)
        self.assertIn("pid=$launcher.Id", SOURCE)
        self.assertIn("startedTicks=$launcher.StartTime.ToUniversalTime().Ticks", SOURCE)

    def test_stop_remains_scoped_to_the_recorded_launcher(self):
        self.assertIn("Stop-Process -Id $existing.Id", SOURCE)
        self.assertNotIn("wsl --shutdown", SOURCE.lower())
        self.assertNotIn("wsl --terminate", SOURCE.lower())
