def payload(payer="owner-a", note="消费"):
    return {
        "note": note,
        "participants": [payer],
        "settlement_currency": "CNY",
        "expenses": [
            {
                "expense_id": "test-expense",
                "payer_id": payer,
                "items": [
                    {
                        "item_id": "test-item",
                        "name": note,
                        "amount": {"currency": "CNY", "amount": "20.00"},
                        "allocation": {payer: "1"},
                    }
                ],
            }
        ],
    }
