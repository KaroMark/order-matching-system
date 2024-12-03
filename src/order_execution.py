from collections import deque
import json
import os
from datetime import datetime

class OrderBook:
    def __init__(self, stock_info, unmatched_orders_file='unmatched_orders.json', executed_trades_file='executed_trades.json'):
        self.stock_info = stock_info
        self.buy_orders = {}   # {ticker: deque of buy orders}
        self.sell_orders = {}  # {ticker: deque of sell orders}
        self.last_trade_price = {}  # {ticker: last execution price}
        self.unmatched_orders_file = unmatched_orders_file
        self.executed_trades_file = executed_trades_file

        self.load_unmatched_orders()

    def load_unmatched_orders(self):
        if os.path.exists(self.unmatched_orders_file):
            with open(self.unmatched_orders_file, 'r') as f:
                data = json.load(f)
                self.buy_orders = {}
                self.sell_orders = {}
                for ticker, orders in data.get('buy_orders', {}).items():
                    self.buy_orders[ticker] = deque()
                    for order in orders:
                        # Convert 'timestamp' from string to datetime
                        order['timestamp'] = datetime.fromisoformat(order['timestamp'])
                        self.buy_orders[ticker].append(order)
                for ticker, orders in data.get('sell_orders', {}).items():
                    self.sell_orders[ticker] = deque()
                    for order in orders:
                        order['timestamp'] = datetime.fromisoformat(order['timestamp'])
                        self.sell_orders[ticker].append(order)
        else:
            self.buy_orders = {}
            self.sell_orders = {}

    def save_unmatched_orders(self):
        def serialize_order(order):
            order_copy = order.copy()
            order_copy['timestamp'] = order_copy['timestamp'].isoformat()
            return order_copy

        data = {
            'buy_orders': {ticker: [serialize_order(order) for order in orders] for ticker, orders in self.buy_orders.items()},
            'sell_orders': {ticker: [serialize_order(order) for order in orders] for ticker, orders in self.sell_orders.items()}
        }
        with open(self.unmatched_orders_file, 'w') as f:
            json.dump(data, f, indent=4)

    def save_executed_trade(self, trade_info):
        # Append trade_info to the executed trades file
        try:
            with open(self.executed_trades_file, 'r') as f:
                executed_trades = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            executed_trades = []

        executed_trades.append(trade_info)
        with open(self.executed_trades_file, 'w') as f:
            json.dump(executed_trades, f, indent=4)

    def get_best_price(self, action, ticker):
        if action == 'buy':
            # Best price is the lowest price from sell limit orders
            if ticker in self.sell_orders:
                limit_orders = [o for o in self.sell_orders[ticker] if o['order_type'] == 'limit']
                if limit_orders:
                    return min(o['price'] for o in limit_orders)
        elif action == 'sell':
            # Best price is the highest price from buy limit orders
            if ticker in self.buy_orders:
                limit_orders = [o for o in self.buy_orders[ticker] if o['order_type'] == 'limit']
                if limit_orders:
                    return max(o['price'] for o in limit_orders)
        return self.last_trade_price.get(ticker, self.stock_info.get_initial_price(ticker))

    def add_order(self, order, account_manager):
        ticker = order['ticker']
        account_id = order['account_id']
        account = account_manager.get_account(account_id)

        # Validate order
        if order['quantity'] <= 0:
            print("Error: Quantity must be positive.")
            return False
        if order['order_type'] not in ['market', 'limit']:
            print("Error: Order type must be 'market' or 'limit'.")
            return False
        if order['order_type'] == 'limit' and (order['price'] is None or order['price'] <= 0):
            print("Error: Limit orders require a positive price.")
            return False

        # Remove validation to allow short selling
        # (Commented out the block that prevents selling more shares than owned)
        if order['action'] == 'sell':
            positions = account['positions']
            if positions.get(ticker, 0) < order['quantity']:
                print(f"Error: Account {account_id} does not have enough shares to sell.")
                return False  # Do not add the order

        # Add order to the appropriate order book
        if order['action'] == 'buy':
            if ticker not in self.buy_orders:
                self.buy_orders[ticker] = deque()
            self.buy_orders[ticker].append(order)
        elif order['action'] == 'sell':
            if ticker not in self.sell_orders:
                self.sell_orders[ticker] = deque()
            self.sell_orders[ticker].append(order)

        print("Order added to the order book.")
        self.save_unmatched_orders()
        return True

    def match_orders(self, ticker, account_manager):
        buy_orders = self.buy_orders.get(ticker, deque())
        sell_orders = self.sell_orders.get(ticker, deque())

        # Sort orders
        buy_orders = deque(sorted(buy_orders, key=lambda o: (
            o['order_type'] != 'market',
            -o.get('price', float('inf')) if o.get('price') else float('inf'),
            o['timestamp']
        )))
        sell_orders = deque(sorted(sell_orders, key=lambda o: (
            o['order_type'] != 'market',
            o.get('price', 0) if o.get('price') else 0,
            o['timestamp']
        )))

        # Try to match orders
        while buy_orders and sell_orders:
            matched = False
            for buy_order in list(buy_orders):
                for sell_order in list(sell_orders):
                    if buy_order['account_id'] == sell_order['account_id']:
                        continue  # Skip matching orders from the same account

                    execution_price = None

                    # Determine execution price
                    if buy_order['order_type'] == 'market' and sell_order['order_type'] == 'market':
                        # Use last trade price or cannot match
                        execution_price = self.last_trade_price.get(ticker)
                        if execution_price is None:
                            # Can't determine price, skip this pair
                            continue
                    elif buy_order['order_type'] == 'market' and sell_order['order_type'] == 'limit':
                        execution_price = sell_order['price']
                    elif buy_order['order_type'] == 'limit' and sell_order['order_type'] == 'market':
                        execution_price = buy_order['price']
                    elif buy_order['order_type'] == 'limit' and sell_order['order_type'] == 'limit':
                        if buy_order['price'] >= sell_order['price']:
                            execution_price = sell_order['price']
                        else:
                            continue  # Prices do not overlap
                    else:
                        continue  # Invalid order types

                    # Determine quantity to execute
                    exec_quantity = min(buy_order['quantity'], sell_order['quantity'])

                    # Update buyer's account
                    buyer_account = account_manager.get_account(buy_order['account_id'])
                    total_cost = exec_quantity * execution_price

                    if buyer_account['balance'] >= total_cost:
                        buyer_account['balance'] -= total_cost
                        buyer_positions = buyer_account['positions']
                        buyer_positions[ticker] = buyer_positions.get(ticker, 0) + exec_quantity
                        account_manager.update_account(buy_order['account_id'], buyer_account)
                    else:
                        print(f"Account {buy_order['account_id']} has insufficient balance.")
                        buy_orders.remove(buy_order)
                        matched = True
                        break  # Proceed to next buy order

                    # Update seller's account
                    seller_account = account_manager.get_account(sell_order['account_id'])
                    seller_positions = seller_account['positions']
                    seller_positions[ticker] = seller_positions.get(ticker, 0) - exec_quantity
                    seller_account['balance'] += total_cost
                    if seller_positions.get(ticker, 0) == 0:
                        del seller_positions[ticker]
                    account_manager.update_account(sell_order['account_id'], seller_account)

                    # Update order quantities
                    buy_order['quantity'] -= exec_quantity
                    sell_order['quantity'] -= exec_quantity

                    # Update last trade price
                    self.last_trade_price[ticker] = execution_price

                    print(f"Executed {exec_quantity} shares of {ticker} at {execution_price} between Account {buy_order['account_id']} (buy) and Account {sell_order['account_id']} (sell).")

                    # Save executed trade
                    trade_info = {
                        'ticker': ticker,
                        'price': execution_price,
                        'quantity': exec_quantity,
                        'buy_account_id': buy_order['account_id'],
                        'sell_account_id': sell_order['account_id'],
                        'timestamp': datetime.now().isoformat()
                    }
                    self.save_executed_trade(trade_info)

                    # Remove filled orders
                    if buy_order['quantity'] == 0:
                        buy_orders.remove(buy_order)
                    if sell_order['quantity'] == 0:
                        sell_orders.remove(sell_order)

                    matched = True
                    break  # Break inner loop to proceed with updated order lists

                if matched:
                    break  # Break outer loop to restart matching with updated lists

            if not matched:
                break  # No more matches possible

        # Update the order books
        self.buy_orders[ticker] = buy_orders
        self.sell_orders[ticker] = sell_orders

        # Save unmatched orders
        self.save_unmatched_orders()

    def display_order_book(self):
        print("Order Book:")
        for ticker in set(self.buy_orders.keys()).union(self.sell_orders.keys()):
            print(f"\nTicker: {ticker}")
            print("Buy Orders:")
            for order in self.buy_orders.get(ticker, []):
                price_display = 'Market' if order['order_type'] == 'market' else order['price']
                print(f"  Account {order['account_id']} wants to buy {order['quantity']} at {price_display}")
            print("Sell Orders:")
            for order in self.sell_orders.get(ticker, []):
                price_display = 'Market' if order['order_type'] == 'market' else order['price']
                print(f"  Account {order['account_id']} wants to sell {order['quantity']} at {price_display}")

    def display_executed_trades(self):
        try:
            with open(self.executed_trades_file, 'r') as f:
                executed_trades = json.load(f)
                if not executed_trades:
                    print("No executed trades found.")
                    return
                print("Executed Trades:")
                for trade in executed_trades:
                    print(f"Timestamp: {trade.get('timestamp', 'N/A')}")
                    print(f"  Ticker: {trade.get('ticker', 'N/A')}")
                    print(f"  Price: {trade.get('price', 'N/A')}")
                    print(f"  Quantity: {trade.get('quantity', 'N/A')}")
                    print(f"  Buyer Account ID: {trade.get('buy_account_id', 'N/A')}")
                    print(f"  Seller Account ID: {trade.get('sell_account_id', 'N/A')}")
                    print()
        except (FileNotFoundError, json.JSONDecodeError):
            print("No executed trades found.")

    def export_executed_trades(self, filename):
        try:
            with open(self.executed_trades_file, 'r') as f:
                executed_trades = json.load(f)
                if not executed_trades:
                    print("No executed trades to export.")
                    return
                with open(filename, 'w') as out_file:
                    for trade in executed_trades:
                        out_file.write(f"Timestamp: {trade.get('timestamp', 'N/A')}\n")
                        out_file.write(f"  Ticker: {trade.get('ticker', 'N/A')}\n")
                        out_file.write(f"  Price: {trade.get('price', 'N/A')}\n")
                        out_file.write(f"  Quantity: {trade.get('quantity', 'N/A')}\n")
                        out_file.write(f"  Buyer Account ID: {trade.get('buy_account_id', 'N/A')}\n")
                        out_file.write(f"  Seller Account ID: {trade.get('sell_account_id', 'N/A')}\n")
                        out_file.write("\n")
                print(f"Executed trades exported to {filename}.")
        except (FileNotFoundError, json.JSONDecodeError):
            print("No executed trades found.")

    def get_best_bid_ask(self, ticker):
        # Get best bid (highest buy price) and best ask (lowest sell price)
        best_bid = None
        best_ask = None

        if ticker in self.buy_orders:
            limit_buy_orders = [o for o in self.buy_orders[ticker] if o['order_type'] == 'limit']
            if limit_buy_orders:
                best_bid = max(o['price'] for o in limit_buy_orders)
        if ticker in self.sell_orders:
            limit_sell_orders = [o for o in self.sell_orders[ticker] if o['order_type'] == 'limit']
            if limit_sell_orders:
                best_ask = min(o['price'] for o in limit_sell_orders)
        return best_bid, best_ask
