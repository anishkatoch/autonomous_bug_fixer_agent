"""
Cost Tracker - Monitor and limit LLM API spending
Simple functions to track costs and stay under budget!

Functions:
- init_cost_tracker(): Initialize tracking
- track_tokens(): Record token usage
- get_cost_summary(): Get current spending
- is_over_budget(): Check if budget exceeded
- calculate_cost(): Compute cost from tokens
"""

from logger_setup import log_info, log_warning, log_cost_update


# Global cost tracking
_cost_data = {
    'budget_limit': 5.0,
    'total_cost': 0.0,
    'total_tokens': 0,
    'requests': [],
    'provider': None,
    'pricing': {}
}


def init_cost_tracker(budget_limit, provider='anthropic'):
    """
    Initialize the cost tracker

    What it does:
    1. Sets budget limit
    2. Loads pricing for the LLM provider
    3. Resets all counters
    4. Logs initialization

    Args:
        budget_limit: Maximum allowed cost in USD
        provider: 'anthropic' or 'openai'
    """
    global _cost_data

    _cost_data['budget_limit'] = budget_limit
    _cost_data['provider'] = provider
    _cost_data['total_cost'] = 0.0
    _cost_data['total_tokens'] = 0
    _cost_data['requests'] = []

    _set_pricing(provider)

    log_info(f"Cost tracker initialized: ${budget_limit:.2f} budget ({provider})")


def switch_provider(provider):
    """Switch cost tracking to a different provider (used during fallback)"""
    global _cost_data
    _cost_data['provider'] = provider
    _set_pricing(provider)
    log_info(f"Cost tracker switched to {provider} pricing")


def _set_pricing(provider):
    """Set pricing based on provider"""
    global _cost_data
    if provider == 'anthropic':
        _cost_data['pricing'] = {
            'input': 3.00,   # $3 per 1M input tokens
            'output': 15.00  # $15 per 1M output tokens
        }
    else:  # openai
        _cost_data['pricing'] = {
            'input': 10.00,   # $10 per 1M input tokens
            'output': 30.00   # $30 per 1M output tokens
        }


def track_tokens(input_tokens, output_tokens, context=""):
    """
    Track token usage and calculate cost
    
    What it does:
    1. Calculates cost based on token counts
    2. Adds to running total
    3. Records request details
    4. Checks budget limit
    5. Logs cost update
    
    Args:
        input_tokens: Number of input tokens used
        output_tokens: Number of output tokens generated
        context: Description of what this call was for
    
    Returns:
        dict: {
            'tokens': total_tokens,
            'cost': cost_for_this_call,
            'total_cost': running_total,
            'budget_remaining': how_much_left
        }
    """
    global _cost_data
    
    # Calculate cost
    cost = calculate_cost(input_tokens, output_tokens)
    
    # Update totals
    _cost_data['total_cost'] += cost
    _cost_data['total_tokens'] += (input_tokens + output_tokens)
    
    # Record request
    _cost_data['requests'].append({
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'cost': cost,
        'context': context
    })
    
    # Calculate remaining budget
    remaining = _cost_data['budget_limit'] - _cost_data['total_cost']
    
    # Log cost update
    log_cost_update(
        input_tokens + output_tokens,
        cost,
        _cost_data['total_cost'],
        remaining
    )
    
    # Warn if over budget
    if remaining < 0:
        log_warning(f"BUDGET EXCEEDED! Over by ${-remaining:.2f}")
    
    return {
        'tokens': input_tokens + output_tokens,
        'cost': cost,
        'total_cost': _cost_data['total_cost'],
        'budget_remaining': remaining
    }


def calculate_cost(input_tokens, output_tokens):
    """
    Calculate cost from token counts
    
    What it does:
    1. Gets pricing for current provider
    2. Calculates input cost
    3. Calculates output cost
    4. Returns total
    
    Args:
        input_tokens: Input token count
        output_tokens: Output token count
    
    Returns:
        float: Cost in USD
    """
    pricing = _cost_data['pricing']
    
    # Cost = (tokens / 1,000,000) * price_per_million
    input_cost = (input_tokens / 1_000_000) * pricing['input']
    output_cost = (output_tokens / 1_000_000) * pricing['output']
    
    return input_cost + output_cost


def is_over_budget():
    """
    Check if budget has been exceeded
    
    What it does:
    1. Compares total cost to budget limit
    2. Returns True if over
    
    Returns:
        bool: True if over budget
    """
    return _cost_data['total_cost'] >= _cost_data['budget_limit']


def get_budget_remaining():
    """
    Get remaining budget
    
    What it does:
    1. Calculates how much budget is left
    2. Returns 0 if already over
    
    Returns:
        float: Remaining budget in USD
    """
    remaining = _cost_data['budget_limit'] - _cost_data['total_cost']
    return max(0, remaining)


def get_cost_summary():
    """
    Get complete cost summary
    
    What it does:
    1. Compiles all cost data
    2. Calculates averages
    3. Returns comprehensive summary
    
    Returns:
        dict: {
            'total_cost': float,
            'total_tokens': int,
            'budget_limit': float,
            'budget_remaining': float,
            'total_requests': int,
            'average_cost_per_request': float,
            'over_budget': bool,
            'provider': str
        }
    """
    total_requests = len(_cost_data['requests'])
    avg_cost = _cost_data['total_cost'] / total_requests if total_requests > 0 else 0
    
    return {
        'total_cost': _cost_data['total_cost'],
        'total_tokens': _cost_data['total_tokens'],
        'budget_limit': _cost_data['budget_limit'],
        'budget_remaining': get_budget_remaining(),
        'total_requests': total_requests,
        'average_cost_per_request': avg_cost,
        'over_budget': is_over_budget(),
        'provider': _cost_data['provider']
    }


def log_cost(message):
    """
    Log a cost-related message
    
    What it does:
    1. Formats cost information
    2. Logs to console and file
    
    Args:
        message: Cost message to log
    """
    summary = get_cost_summary()
    log_info(f"💰 {message} | Total: ${summary['total_cost']:.4f} | "
             f"Remaining: ${summary['budget_remaining']:.2f}")


def estimate_cost(estimated_tokens):
    """
    Estimate cost for a planned operation
    
    What it does:
    1. Takes estimated token count
    2. Calculates approximate cost
    3. Useful for planning ahead
    
    Args:
        estimated_tokens: Expected token usage
    
    Returns:
        float: Estimated cost in USD
    """
    # Assume 70% input, 30% output (typical ratio)
    input_tokens = int(estimated_tokens * 0.7)
    output_tokens = int(estimated_tokens * 0.3)
    
    return calculate_cost(input_tokens, output_tokens)
