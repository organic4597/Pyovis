use crossbeam_channel::{bounded, Receiver, Sender};
use std::sync::Arc;
use std::thread;

pub struct ThreadPool {
    workers: Vec<Worker>,
    sender: Sender<Job>,
}

type Job = Box<dyn FnOnce() + Send + 'static>;

impl ThreadPool {
    /// core_ids: 이 풀에 배정할 CPU 코어 목록
    pub fn new(size: usize, core_ids: Vec<usize>) -> Self {
        let (sender, receiver) = bounded(1024);
        let receiver = Arc::new(receiver);
        let mut workers = Vec::with_capacity(size);

        for (i, core_id) in core_ids.iter().enumerate().take(size) {
            workers.push(Worker::new(i, Arc::clone(&receiver), *core_id));
        }

        ThreadPool { workers, sender }
    }

    pub fn execute<F>(&self, f: F)
    where
        F: FnOnce() + Send + 'static,
    {
        self.sender
            .send(Box::new(f))
            .expect("Thread pool send failed");
    }

    pub fn shutdown(self) {
        drop(self.sender);
        for worker in self.workers {
            if let Some(thread) = worker.thread {
                thread.join().expect("Worker thread panicked");
            }
        }
    }
}

struct Worker {
    id: usize,
    thread: Option<thread::JoinHandle<()>>,
}

impl Worker {
    fn new(id: usize, receiver: Arc<Receiver<Job>>, core_id: usize) -> Worker {
        let thread = thread::spawn(move || {
            // CPU Affinity 설정 (Linux)
            #[cfg(target_os = "linux")]
            {
                unsafe {
                    let mut cpuset = std::mem::zeroed::<libc::cpu_set_t>();
                    libc::CPU_SET(core_id, &mut cpuset);
                    libc::sched_setaffinity(0, std::mem::size_of::<libc::cpu_set_t>(), &cpuset);
                }
            }

            loop {
                match receiver.recv() {
                    Ok(job) => job(),
                    Err(_) => break,
                }
            }
        });

        Worker {
            id,
            thread: Some(thread),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn test_thread_pool() {
        let pool = ThreadPool::new(2, vec![0, 1]);
        let counter = Arc::new(AtomicUsize::new(0));

        for _ in 0..10 {
            let counter = Arc::clone(&counter);
            pool.execute(move || {
                counter.fetch_add(1, Ordering::Relaxed);
            });
        }

        // Give some time for tasks to complete
        thread::sleep(std::time::Duration::from_millis(100));

        assert_eq!(counter.load(Ordering::Relaxed), 10);
    }
}
