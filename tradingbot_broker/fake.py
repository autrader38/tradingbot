"""Scriptable in-memory broker; acceptance does not manufacture fills."""

from .broker import SimulatedBroker
from .models import AccountSnapshot, OrderState, TradingMode


class FakeBroker(SimulatedBroker):
    def __init__(self, account: AccountSnapshot, *, mode=TradingMode.PAPER,
                 reject_orders=False, confirm_cancellations=True):
        if not isinstance(account, AccountSnapshot):
            raise TypeError('Explicit fake account snapshot required')
        if type(reject_orders) is not bool or type(confirm_cancellations) is not bool:
            raise TypeError('Explicit boolean fake controls required')
        super().__init__(mode)
        self.reported_account = account
        self.reject_orders = reject_orders
        self.confirm_cancellations = confirm_cancellations
        self.submissions = []
        self.cancellations = []
        self.modifications = []
        self._next_id = 1

    def _backend_connect(self, at):
        return self.reported_account

    def _backend_disconnect(self):
        pass

    def _backend_place(self, order):
        order_id = f'fake-{self._next_id}'
        self._next_id += 1
        self.submissions.append(order)
        return order_id, (OrderState.REJECTED if self.reject_orders else OrderState.ACKNOWLEDGED), (201 if self.reject_orders else None)

    def _backend_cancel(self, order_id):
        self.cancellations.append(order_id)
        return self.confirm_cancellations

    def _backend_replace(self, order_id, order):
        self.modifications.append((order_id, order))
        return not self.reject_orders
