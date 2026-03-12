#![allow(non_local_definitions)]

use pyo3::prelude::*;
use std::sync::Arc;

mod model;
mod queue;
#[allow(dead_code)]
mod thread_pool;

use model::hot_swap::ModelHotSwap;
use queue::priority_queue::{PriorityTaskQueue, Task, TaskPriority};

#[pyclass]
struct PyPriorityQueue {
    inner: Arc<PriorityTaskQueue>,
}

#[pymethods]
impl PyPriorityQueue {
    #[new]
    fn new() -> Self {
        Self {
            inner: Arc::new(PriorityTaskQueue::new()),
        }
    }

    fn enqueue(&self, priority: u8, task_type: &str, payload: &str) {
        let priority = match priority {
            0 => TaskPriority::Stop,
            1 => TaskPriority::AiBrain,
            2 => TaskPriority::AiHands,
            3 => TaskPriority::AiJudge,
            4 => TaskPriority::Orchestration,
            _ => TaskPriority::Io,
        };
        let task = Task::new(priority, task_type, payload);
        self.inner.enqueue(task);
    }

    fn dequeue(&self) -> Option<(u8, String, String)> {
        self.inner.dequeue().map(|task| {
            let priority_num = match task.priority {
                TaskPriority::Stop => 0,
                TaskPriority::AiBrain => 1,
                TaskPriority::AiHands => 2,
                TaskPriority::AiJudge => 3,
                TaskPriority::Orchestration => 4,
                TaskPriority::Io => 5,
            };
            (priority_num, task.task_type, task.payload)
        })
    }

    fn len(&self) -> usize {
        self.inner.len()
    }

    fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }
}

#[pyclass]
struct PyModelSwap {
    inner: Arc<ModelHotSwap>,
}

#[pymethods]
impl PyModelSwap {
    #[new]
    fn new() -> Self {
        Self {
            inner: Arc::new(ModelHotSwap::new()),
        }
    }

    fn switch_to_planner(&self) -> (String, bool) {
        let result = self.inner.switch_to_planner();
        (
            format!("{:?}", result.new_role),
            result.requires_server_restart,
        )
    }

    fn switch_to_brain(&self) -> (String, bool) {
        let result = self.inner.switch_to_brain();
        (
            format!("{:?}", result.new_role),
            result.requires_server_restart,
        )
    }

    fn switch_to_hands(&self) -> (String, bool) {
        let result = self.inner.switch_to_hands();
        (
            format!("{:?}", result.new_role),
            result.requires_server_restart,
        )
    }

    fn switch_to_judge(&self) -> (String, bool) {
        let result = self.inner.switch_to_judge();
        (
            format!("{:?}", result.new_role),
            result.requires_server_restart,
        )
    }

    fn current_role(&self) -> String {
        format!("{:?}", self.inner.current_role())
    }
}

#[pymodule]
fn pyovis_core(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<PyPriorityQueue>()?;
    m.add_class::<PyModelSwap>()?;
    Ok(())
}
