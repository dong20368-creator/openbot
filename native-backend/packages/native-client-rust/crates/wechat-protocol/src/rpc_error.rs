//! Non-sensitive stages for ordinary authenticated RPC failures.
use crate::calls::host_transport::{CallHostRpcError, CallHostStage};
use std::time::{Duration, Instant};

#[derive(Clone, Debug)]
pub struct RpcError {
    pub stage: CallHostStage,
    pub code: &'static str,
    pub value: Option<i64>,
    pub detail: Option<&'static str>,
}
impl RpcError {
    pub(crate) fn at(stage: CallHostStage, cause: &anyhow::Error) -> Self {
        let safe = CallHostRpcError::from_cause(stage, cause);
        let detail = cause.chain().find_map(|e| match e.to_string().as_str() {
            "handshake_signature_record_authentication" => Some("signature_record_authentication"),
            "handshake_pinned_signature_verification" => Some("pinned_signature_verification"),
            "handshake_ticket_record_authentication" => Some("ticket_record_authentication"),
            "handshake_finished_record_authentication" => Some("finished_record_authentication"),
            "handshake_noop_record_authentication" => Some("noop_record_authentication"),
            _ => None,
        });
        Self {
            stage,
            code: safe.code,
            value: safe.value,
            detail,
        }
    }
    pub fn before_submission(&self) -> bool {
        matches!(
            self.stage,
            CallHostStage::Handshake | CallHostStage::Prepare
        )
    }
}
impl std::fmt::Display for RpcError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "RPC {} failed: {}", self.stage.as_str(), self.code)
    }
}
impl std::error::Error for RpcError {}

pub(crate) fn connect_before_submission<T>(
    timeout: Duration,
    mut connect: impl FnMut(Duration) -> anyhow::Result<T>,
) -> Result<T, RpcError> {
    let started = Instant::now();
    let first = connect(timeout).map_err(|e| RpcError::at(CallHostStage::Handshake, &e));
    // Only a fresh handshake is retried, before preparing or submitting any
    // business request. Authentication and certificate checks remain intact.
    if first.as_ref().is_err_and(|e| {
        matches!(
            e.code,
            "openssl_crypto"
                | "unexpected_eof"
                | "connection_reset"
                | "connection_aborted"
                | "connection_closed"
                | "connect_failed"
        )
    }) {
        let remaining = timeout.saturating_sub(started.elapsed());
        if !remaining.is_zero() {
            return connect(remaining).map_err(|e| RpcError::at(CallHostStage::Handshake, &e));
        }
    }
    first
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn transient_handshake_retries_once_before_any_request() {
        let mut count = 0;
        let result = connect_before_submission(Duration::from_secs(1), |_| {
            count += 1;
            if count == 1 {
                Err(std::io::Error::from(std::io::ErrorKind::ConnectionReset).into())
            } else {
                Ok(7)
            }
        });
        assert_eq!(result.unwrap(), 7);
        assert_eq!(count, 2);
        count = 0;
        let result = connect_before_submission::<()>(Duration::from_secs(1), |_| {
            count += 1;
            Err(std::io::Error::from(std::io::ErrorKind::ConnectionReset).into())
        });
        assert!(result.unwrap_err().before_submission());
        assert_eq!(count, 2);
    }
    #[test]
    fn verification_failure_is_not_retried_and_exchange_is_not_unsent() {
        let mut count = 0;
        let e = connect_before_submission::<()>(Duration::from_secs(1), |_| {
            count += 1;
            Err(anyhow::anyhow!(
                "Pinned server signature verification failed"
            ))
        })
        .unwrap_err();
        assert_eq!(count, 1);
        assert_eq!(e.code, "signature_verification");
        let e = RpcError::at(
            CallHostStage::Exchange,
            &anyhow::anyhow!("private-key-and-message"),
        );
        assert!(!e.before_submission());
        assert!(!e.to_string().contains("private"));
        assert!(
            !RpcError::at(CallHostStage::Decode, &anyhow::anyhow!("invalid")).before_submission()
        );
    }
}
