# myPOS Live Switch Checklist

- Confirm the `myPOS` store website matches the customer-facing frontend domain.
- Confirm the store is verified and uses the live `Configuration Pack`.
- Set `MYPOS_MODE=live`.
- Set the live `MYPOS_CONFIGURATION_PACK` or live manual key fields.
- Verify `BASE_URL` points to the public HTTPS backend domain.
- Verify `FRONTEND_URL` points to the public frontend domain.
- Verify `URL_Notify`, `URL_OK`, and `URL_Cancel` resolve publicly over HTTPS.
- Keep `MYPOS_RECURRING_ENABLED=false` for launch unless myPOS explicitly enables recurring payments.
- Keep `MYPOS_REQUEST_CARD_TOKEN=false` unless recurring is enabled and intentionally being used.
- Run one live low-value payment and confirm:
  - checkout opens without browser trust warnings
  - `IPCPurchaseNotify` marks the order as `paid`
  - the subscription becomes active exactly once
  - the journal opens only after active subscription is confirmed
