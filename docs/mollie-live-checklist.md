# Mollie live checklist

## Mollie account

- Complete organization, stakeholder, identity, and bank-account verification.
- Register the exact customer-facing website URL in the OM & Nutrition profile.
- Activate credit/debit cards and SEPA Direct Debit for the profile.
- Confirm the profile can return at least one method for `sequenceType=first`.
- Create a standard live API key scoped to the same website profile.

## Website

- Display the total price of EUR 22 per month before checkout.
- Clearly disclose automatic monthly renewal and cancellation terms.
- Publish the final privacy policy and subscription-specific terms approved by the publisher.
- Keep the legal business name, address, and customer contact details visible.

## Server

- Set `MOLLIE_MODE=live`.
- Replace `MOLLIE_API_KEY` with the `live_...` key.
- Keep `MOLLIE_MONTHLY_AMOUNT=22.00` and `MOLLIE_CURRENCY=EUR`.
- Confirm `BASE_URL=https://ohm.cod-st.com`.
- Confirm `FRONTEND_URL` matches the website profile URL.
- Restart only `integrative-backend.service`.

## Verification

- Complete one real EUR 22 card payment and verify the order is marked paid.
- Confirm exactly one Mollie subscription is created for the customer.
- Confirm the account can open issue details and the PDF after payment.
- Confirm a repeated webhook does not extend access or create another subscription.
- Cancel the subscription and confirm access remains until the paid period ends.
- Test failed, canceled, refunded, and chargeback states before public launch.
