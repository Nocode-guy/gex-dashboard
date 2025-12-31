"""Test Tradier API connection and gamma data."""
import asyncio
from tradier_client import get_tradier_client

async def test():
    client = get_tradier_client()
    print('Testing Tradier API...')
    print(f'Base URL: {client.base_url}')
    print(f'Using mock: {client.use_mock}')

    # Test quote
    print('\n--- Testing Quote ---')
    quote = await client.get_quote('SPY')
    if quote:
        print(f'SPY Quote: ${quote.get("last", "N/A")}')
    else:
        print('ERROR: Could not get quote')
        return

    # Test options chain with greeks
    print('\n--- Fetching Options Chain with Greeks ---')
    spot, contracts = await client.get_full_chain_with_greeks('SPY', max_expirations=2)

    print(f'Spot Price: ${spot}')
    print(f'Total Contracts: {len(contracts)}')

    if contracts:
        # Show sample contracts with gamma
        print('\nSample contracts with Gamma:')
        for c in contracts[:10]:
            print(f'  {c.option_type.upper():4} ${c.strike:>7.2f} exp:{c.expiration} | Gamma: {c.gamma:.6f} | Delta: {c.delta:>7.4f} | OI: {c.open_interest:>6}')

        # Check if gamma values are real (not zero)
        gammas = [c.gamma for c in contracts if c.gamma != 0]
        print(f'\nContracts with non-zero gamma: {len(gammas)} / {len(contracts)}')
        if gammas:
            print(f'Gamma range: {min(gammas):.6f} to {max(gammas):.6f}')
            print('\n*** GAMMA DATA IS COMING THROUGH! ***')
        else:
            print('\nWARNING: All gamma values are zero!')
    else:
        print('ERROR: No contracts returned')

if __name__ == '__main__':
    asyncio.run(test())
