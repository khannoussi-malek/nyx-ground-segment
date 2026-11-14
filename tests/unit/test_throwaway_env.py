from tests.support.compose import throwaway_env_text


def test_strips_only_the_project_name() -> None:
    env = "A=1\nCOMPOSE_PROJECT_NAME=nyx-alarms\nGS_IMAGE_TAG=alarms\nXCOMPOSE_PROJECT_NAME=k\n"
    assert throwaway_env_text(env) == "A=1\nGS_IMAGE_TAG=alarms\nXCOMPOSE_PROJECT_NAME=k\n"
