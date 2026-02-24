use crossbeam::queue::SegQueue;
use std::sync::atomic::{AtomicUsize, Ordering};

#[derive(Debug, Clone, PartialEq)]
pub enum TaskPriority {
    Stop = 0,          // P0: 긴급 중단 (항상 최우선)
    AiBrain = 1,       // P1: Brain 추론
    AiHands = 2,       // P2: Hands 코드 생성
    AiJudge = 3,       // P3: Judge 평가
    Orchestration = 4, // P4: 오케스트레이션
    Io = 5,            // P5: IO 작업
}

#[derive(Debug, Clone)]
pub struct Task {
    pub priority: TaskPriority,
    pub task_type: String,
    pub payload: String,
}

impl Task {
    pub fn new(priority: TaskPriority, task_type: &str, payload: &str) -> Self {
        Self {
            priority,
            task_type: task_type.to_string(),
            payload: payload.to_string(),
        }
    }
}

pub struct PriorityTaskQueue {
    stop_queue: SegQueue<Task>,
    ai_queue: SegQueue<Task>,
    io_queue: SegQueue<Task>,
    total_size: AtomicUsize,
}

impl PriorityTaskQueue {
    pub fn new() -> Self {
        Self {
            stop_queue: SegQueue::new(),
            ai_queue: SegQueue::new(),
            io_queue: SegQueue::new(),
            total_size: AtomicUsize::new(0),
        }
    }

    pub fn enqueue(&self, task: Task) {
        self.total_size.fetch_add(1, Ordering::Relaxed);
        match task.priority {
            TaskPriority::Stop => self.stop_queue.push(task),
            TaskPriority::AiBrain
            | TaskPriority::AiHands
            | TaskPriority::AiJudge
            | TaskPriority::Orchestration => self.ai_queue.push(task),
            TaskPriority::Io => self.io_queue.push(task),
        }
    }

    /// P0 → P1 → P2 순서로 dequeue
    pub fn dequeue(&self) -> Option<Task> {
        self.stop_queue
            .pop()
            .or_else(|| self.ai_queue.pop())
            .or_else(|| self.io_queue.pop())
            .map(|task| {
                self.total_size.fetch_sub(1, Ordering::Relaxed);
                task
            })
    }

    pub fn len(&self) -> usize {
        self.total_size.load(Ordering::Relaxed)
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

impl Default for PriorityTaskQueue {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_priority_queue() {
        let queue = PriorityTaskQueue::new();

        // Add tasks in reverse priority order
        queue.enqueue(Task::new(TaskPriority::Io, "io_task", "io_payload"));
        queue.enqueue(Task::new(
            TaskPriority::AiHands,
            "hands_task",
            "hands_payload",
        ));
        queue.enqueue(Task::new(TaskPriority::Stop, "stop_task", "stop_payload"));

        // Should dequeue in priority order: Stop > AI > IO
        let task = queue.dequeue().unwrap();
        assert_eq!(task.priority, TaskPriority::Stop);

        let task = queue.dequeue().unwrap();
        assert_eq!(task.priority, TaskPriority::AiHands);

        let task = queue.dequeue().unwrap();
        assert_eq!(task.priority, TaskPriority::Io);

        assert!(queue.is_empty());
    }

    #[test]
    fn test_queue_length() {
        let queue = PriorityTaskQueue::new();
        assert_eq!(queue.len(), 0);

        queue.enqueue(Task::new(TaskPriority::Io, "task1", "payload1"));
        queue.enqueue(Task::new(TaskPriority::AiBrain, "task2", "payload2"));
        assert_eq!(queue.len(), 2);

        queue.dequeue();
        assert_eq!(queue.len(), 1);

        queue.dequeue();
        assert_eq!(queue.len(), 0);
    }
}
