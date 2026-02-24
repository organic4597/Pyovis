use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Mutex;

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ModelRole {
    Planner = 0,
    Brain = 1,
    Hands = 2,
    Judge = 3,
}

impl ModelRole {
    /// Each role uses a different model file.
    /// Every role switch requires llama-server restart.
    ///
    /// - Planner: GLM-4.7-Flash
    /// - Brain:   Qwen3-14B
    /// - Hands:   Devstral-24B
    /// - Judge:   DeepSeek-R1-14B
    #[allow(dead_code)] // Used via PyO3 bindings
    pub fn display_name(&self) -> &'static str {
        match self {
            ModelRole::Planner => "planner",
            ModelRole::Brain => "brain",
            ModelRole::Hands => "hands",
            ModelRole::Judge => "judge",
        }
    }
}

impl From<u8> for ModelRole {
    fn from(val: u8) -> Self {
        match val {
            0 => ModelRole::Planner,
            1 => ModelRole::Brain,
            2 => ModelRole::Hands,
            3 => ModelRole::Judge,
            _ => ModelRole::Planner,
        }
    }
}

pub struct ModelHotSwap {
    current_role: AtomicU8,
    switch_lock: Mutex<()>,
}

#[allow(dead_code)]
pub struct SwitchResult {
    pub previous_role: ModelRole,
    pub new_role: ModelRole,
    pub requires_server_restart: bool,
}

impl ModelHotSwap {
    pub fn new() -> Self {
        Self {
            current_role: AtomicU8::new(ModelRole::Planner as u8),
            switch_lock: Mutex::new(()),
        }
    }

    pub fn switch_role(&self, new_role: ModelRole) -> SwitchResult {
        let _lock = self.switch_lock.lock().unwrap();
        let prev_val = self.current_role.swap(new_role as u8, Ordering::SeqCst);
        let prev_role = ModelRole::from(prev_val);

        SwitchResult {
            previous_role: prev_role,
            new_role,
            requires_server_restart: prev_role != new_role,
        }
    }

    pub fn current_role(&self) -> ModelRole {
        ModelRole::from(self.current_role.load(Ordering::SeqCst))
    }

    pub fn switch_to_planner(&self) -> SwitchResult {
        self.switch_role(ModelRole::Planner)
    }

    pub fn switch_to_brain(&self) -> SwitchResult {
        self.switch_role(ModelRole::Brain)
    }

    pub fn switch_to_hands(&self) -> SwitchResult {
        self.switch_role(ModelRole::Hands)
    }

    pub fn switch_to_judge(&self) -> SwitchResult {
        self.switch_role(ModelRole::Judge)
    }
}

impl Default for ModelHotSwap {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_initial_role() {
        let swap = ModelHotSwap::new();
        assert_eq!(swap.current_role(), ModelRole::Planner);
    }

    #[test]
    fn test_switch_always_requires_restart() {
        let swap = ModelHotSwap::new();

        let r = swap.switch_to_brain();
        assert_eq!(r.previous_role, ModelRole::Planner);
        assert_eq!(r.new_role, ModelRole::Brain);
        assert!(r.requires_server_restart);

        let r = swap.switch_to_hands();
        assert!(r.requires_server_restart);

        let r = swap.switch_to_judge();
        assert!(r.requires_server_restart);
    }

    #[test]
    fn test_same_role_no_restart() {
        let swap = ModelHotSwap::new();
        swap.switch_to_brain();
        let r = swap.switch_to_brain();
        assert!(!r.requires_server_restart);
    }

    #[test]
    fn test_role_roundtrip() {
        let swap = ModelHotSwap::new();

        swap.switch_to_brain();
        assert_eq!(swap.current_role(), ModelRole::Brain);

        swap.switch_to_hands();
        assert_eq!(swap.current_role(), ModelRole::Hands);

        swap.switch_to_judge();
        assert_eq!(swap.current_role(), ModelRole::Judge);

        swap.switch_to_planner();
        assert_eq!(swap.current_role(), ModelRole::Planner);
    }

    #[test]
    fn test_planner_role() {
        let swap = ModelHotSwap::new();
        let r = swap.switch_to_planner();
        assert!(!r.requires_server_restart);
        assert_eq!(swap.current_role(), ModelRole::Planner);

        let r = swap.switch_to_brain();
        assert!(r.requires_server_restart);
        assert_eq!(r.previous_role, ModelRole::Planner);
    }
}
