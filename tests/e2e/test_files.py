"""The sandbox of the files of flight software.

Every path flight software names — a target path (/ram0, /tffs0) or a relative name —
lands in the sandbox of its element (`files.sandbox` in the manifest, default the files
directory of the run), never outside it. In the example, `SensorSim.prmDb` saves its
parameter database as /ram0/SensorSimPrmDb.dat through `Os::File`, and `cdh.yaml` puts the
sandbox of Cdh at `./wsts-files`.
"""

from conftest import T

PARAMETER_DATABASE = "ram0/SensorSimPrmDb.dat"


def test_a_saved_parameter_database_lands_in_the_sandbox(gds, wsts_system_simulation):
    simulation = wsts_system_simulation.elements["cdh"]
    sandbox = simulation.directory / "wsts-files"
    database = sandbox / PARAMETER_DATABASE
    database.unlink(missing_ok=True)
    gds.clear_histories()
    gds.send_and_assert_command("SensorSim.prmDb.PRM_SAVE_FILE", [], timeout=T)
    gds.assert_event("SensorSim.prmDb.PrmFileSaveComplete", timeout=T)
    assert database.is_file(), f"{PARAMETER_DATABASE} did not land in the sandbox {sandbox}"
    assert not (simulation.files_dir / "SensorSimPrmDb.dat").exists(), \
        "the database landed in the log directory although the manifest puts the sandbox at ./wsts-files"
