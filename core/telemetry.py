"""
Session telemetry tracker and manifest generation.

Tracks tool usage, LLM token consumption, error rates, and HITL authorization
decisions (auto_approved, operator_approved, operator_rejected, timeout_rejected,
operator_redirected, escalations) for each session. The manifest is written to
disk at session end.
"""
import os
import json
import time
from collections import defaultdict
from typing import Dict, Any

class SessionTelemetry:
    """Tracks tool usage, LLM token consumption, and error rates for a single session."""
    
    def __init__(self, session_dir: str):
        self.session_dir = session_dir
        self.start_time = time.time()
        self.metrics = self._initialize_metrics()
        
    def _initialize_metrics(self) -> Dict[str, Any]:
        return {
            'total_commands_executed': 0,
            'tool_usage': defaultdict(int),
            'command_success_rates': defaultdict(lambda: {'success': 0, 'failure': 0}),
            'rag_validation': {
                'checks_performed': 0,
                'auto_corrected': 0,
                'average_confidence': 0.0,
                'confidence_samples': [],
                'validation_outcomes': {
                    'correctly_validated': 0,
                    'false_positives': 0,
                    'missed_opportunities': 0
                },
                'web_search_stats': {
                    'queries_made': 0,
                    'successful_caches': 0,
                    'fallback_activation_rate': 0.0
                }
            },
            'loop_prevention': {
                'semantic_blocks_triggered': 0,
                'service_pivots_forced': 0
            },
            'llm_metrics': {
                'total_tokens_consumed': 0,
                'total_api_calls': 0,
                'average_response_time_ms': 0,
                'total_response_time_ms': 0, # internal tracking
                'token_efficiency_by_tool': defaultdict(lambda: {'total_tokens': 0, 'calls': 0}),
                'model_performance': defaultdict(lambda: {'total_calls': 0, 'total_tokens': 0, 'total_duration_ms': 0})
            },
            'hitl_decisions': {
                'auto_approved': 0,
                'operator_approved': 0,
                'operator_rejected': 0,
                'operator_redirected': 0,
                'timeout_rejected': 0,
                'escalations': 0,
                'by_tool': defaultdict(lambda: {'approved': 0, 'rejected': 0}),
                'by_risk_level': defaultdict(int)
            }
        }
    
    def record_command_execution(self, tool_name: str, success: bool):
        """Log a tool execution event and update its success/failure counter."""
        self.metrics['total_commands_executed'] += 1
        self.metrics['tool_usage'][tool_name] += 1
        if success:
            self.metrics['command_success_rates'][tool_name]['success'] += 1
        else:
            self.metrics['command_success_rates'][tool_name]['failure'] += 1
            
    def record_validation(self, confidence: float, was_corrected: bool):
        """Log a RAG validation check and track confidence scores over time."""
        self.metrics['rag_validation']['checks_performed'] += 1
        if was_corrected:
            self.metrics['rag_validation']['auto_corrected'] += 1
        self.metrics['rag_validation']['confidence_samples'].append(confidence)
        
    def record_web_search(self, success: bool):
        """Log a web search query and whether it returned useful results."""
        self.metrics['rag_validation']['web_search_stats']['queries_made'] += 1
        if success:
            self.metrics['rag_validation']['web_search_stats']['successful_caches'] += 1
            
    def record_loop_block(self, block_type: str = "semantic"):
        """Log a loop-prevention event (semantic block or forced pivot)."""
        if block_type == "semantic":
            self.metrics['loop_prevention']['semantic_blocks_triggered'] += 1
        elif block_type == "pivot":
            self.metrics['loop_prevention']['service_pivots_forced'] += 1
            
    def record_hitl_decision(self, tool_name: str, decision: str, risk_level: str):
        """Log a HITL authorization decision.
        
        Args:
            tool_name: Name of the tool being evaluated
            decision: One of 'approved', 'rejected', 'redirected', 'auto_approved', 'timeout_rejected'
            risk_level: Risk level at time of decision ('low', 'medium', 'high', 'critical')
        """
        hitl = self.metrics['hitl_decisions']
        
        if decision == 'auto_approved':
            hitl['auto_approved'] += 1
        elif decision == 'approved':
            hitl['operator_approved'] += 1
            hitl['by_tool'][tool_name]['approved'] += 1
        elif decision == 'rejected':
            hitl['operator_rejected'] += 1
            hitl['by_tool'][tool_name]['rejected'] += 1
        elif decision == 'redirected':
            hitl['operator_redirected'] += 1
            hitl['by_tool'][tool_name]['approved'] += 1  # Redirected = approved with modified args
        elif decision == 'timeout_rejected':
            hitl['timeout_rejected'] += 1
            hitl['by_tool'][tool_name]['rejected'] += 1
        
        hitl['by_risk_level'][risk_level] += 1
        
        # Track escalations (any decision at high/critical that wasn't auto_approved)
        if risk_level in ('high', 'critical') and decision != 'auto_approved':
            hitl['escalations'] += 1
    
    def record_llm_call(self, model: str, total_tokens: int, duration_ns: int, context: str = "planning"):
        """Log an LLM API call with token count and latency for cost tracking."""
        # Convert ns to ms
        duration_ms = duration_ns / 1_000_000 if duration_ns else 0
        
        llm = self.metrics['llm_metrics']
        llm['total_api_calls'] += 1
        llm['total_tokens_consumed'] += total_tokens
        llm['total_response_time_ms'] += duration_ms
        
        # Tool efficiency
        eff = llm['token_efficiency_by_tool'][context]
        eff['total_tokens'] += total_tokens
        eff['calls'] += 1
        
        # Model performance
        perf = llm['model_performance'][model]
        perf['total_calls'] += 1
        perf['total_tokens'] += total_tokens
        perf['total_duration_ms'] += duration_ms

    def export_manifest(self) -> dict:
        """Compile all tracked metrics into a structured dictionary for JSON export."""
        # Calculate averages before exporting
        rag = self.metrics['rag_validation']
        if rag['confidence_samples']:
            rag['average_confidence'] = round(sum(rag['confidence_samples']) / len(rag['confidence_samples']), 2)
            
        if rag['web_search_stats']['queries_made'] > 0:
            rate = rag['web_search_stats']['successful_caches'] / rag['web_search_stats']['queries_made']
            rag['web_search_stats']['fallback_activation_rate'] = round(rate, 2)
            
        llm = self.metrics['llm_metrics']
        if llm['total_api_calls'] > 0:
            llm['average_response_time_ms'] = round(llm['total_response_time_ms'] / llm['total_api_calls'], 2)
            
        # Format averages for nested dicts
        formatted_eff = {}
        for tool, stats in llm['token_efficiency_by_tool'].items():
            formatted_eff[tool] = {
                'avg_tokens': round(stats['total_tokens'] / max(1, stats['calls'])),
                'calls': stats['calls']
            }
        
        formatted_perf = {}
        for mod, perf in llm['model_performance'].items():
            formatted_perf[mod] = {
                'total_calls': perf['total_calls'],
                'avg_tokens_per_call': round(perf['total_tokens'] / max(1, perf['total_calls'])),
                'avg_duration_ms': round(perf['total_duration_ms'] / max(1, perf['total_calls']))
            }

        duration_seconds = time.time() - self.start_time
        session_id = os.path.basename(self.session_dir)

        hitl = self.metrics['hitl_decisions']
        
        manifest = {
            "session_id": session_id,
            "duration_seconds": round(duration_seconds, 1),
            "telemetry": {
                "total_commands_executed": self.metrics['total_commands_executed'],
                "tool_usage": dict(self.metrics['tool_usage']),
                "command_success_rates": {k: dict(v) for k, v in self.metrics['command_success_rates'].items()},
                "rag_validation": {
                    "checks_performed": rag['checks_performed'],
                    "auto_corrected": rag['auto_corrected'],
                    "average_confidence": rag.get('average_confidence', 0.0),
                    "validation_outcomes": rag['validation_outcomes'],
                    "web_search_stats": rag['web_search_stats']
                },
                "loop_prevention": self.metrics['loop_prevention'],
                "llm_metrics": {
                    "total_tokens_consumed": llm['total_tokens_consumed'],
                    "total_api_calls": llm['total_api_calls'],
                    "average_response_time_ms": llm.get('average_response_time_ms', 0),
                    "token_efficiency_by_tool": formatted_eff,
                    "model_performance": formatted_perf
                },
                "hitl_decisions": {
                    "auto_approved": hitl['auto_approved'],
                    "operator_approved": hitl['operator_approved'],
                    "operator_rejected": hitl['operator_rejected'],
                    "operator_redirected": hitl['operator_redirected'],
                    "timeout_rejected": hitl['timeout_rejected'],
                    "escalations": hitl['escalations'],
                    "by_tool": {k: dict(v) for k, v in hitl['by_tool'].items()},
                    "by_risk_level": dict(hitl['by_risk_level'])
                }
            }
        }
        return manifest

    def save_manifest(self):
        """Write the session manifest JSON to disk in the session directory."""
        manifest_path = os.path.join(self.session_dir, 'session_manifest.json')
        try:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(self.export_manifest(), f, indent=2)
        except Exception as e:
            print(f"Failed to save telemetry: {e}")

# Global singleton so tools can easily log metrics
session_telemetry = None 

def init_telemetry(session_dir: str):
    """Initialize the global telemetry singleton for the current session."""
    global session_telemetry
    session_telemetry = SessionTelemetry(session_dir)
