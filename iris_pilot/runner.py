from datetime import datetime
from typing import List
from iris_pilot.stages import Stage, StageError

class RunResult:
    def __init__(self):
        self.start_time = datetime.now()
        self.end_time = None
        self.stage_results = []
        self.success = True
        self.failed_stage = None

    def add_result(self, stage_name: str, result: dict):
        self.stage_results.append({
            "stage": stage_name,
            "status": "success",
            "details": result,
            "timestamp": datetime.now()
        })

    def add_error(self, stage_name: str, error: str):
        self.success = False
        self.failed_stage = stage_name
        self.stage_results.append({
            "stage": stage_name,
            "status": "failed",
            "error": error,
            "timestamp": datetime.now()
        })

    def finalize(self):
        self.end_time = datetime.now()

class StageRunner:
    def __init__(self, stages: List[Stage]):
        self.stages = stages

    def run(self) -> RunResult:
        result = RunResult()
        
        for stage in self.stages:
            try:
                stage_result = stage.run()
                result.add_result(stage.name, stage_result)
            except StageError as e:
                result.add_error(stage.name, str(e))
                if stage.is_critical:
                    # Halt on critical failure
                    break
            except Exception as e:
                result.add_error(stage.name, f"Unexpected error: {str(e)}")
                if stage.is_critical:
                    break
        
        result.finalize()
        return result
