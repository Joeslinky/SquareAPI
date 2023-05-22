import requests
import logging
import voluptuous as vol
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.components import webhook
from homeassistant.helpers import config_validation as cv
from dateutil import tz
import datetime

DOMAIN = 'square'
_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_WEBHOOK_ID): cv.string,
                vol.Required("api_key"): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


def setup(hass, config):
    """Set up the JSON webhook."""
    conf = config.get(DOMAIN, {})
    webhook_id = conf.get(CONF_WEBHOOK_ID)
    api_key = conf.get("api_key")
    hass.data[DOMAIN] = {"api_key": api_key}
    hass.components.webhook.async_register(
        DOMAIN, "Square Webhook", webhook_id, handle_webhook
    )
    return True


async def handle_webhook(hass, webhook_id, request):
    """Handle webhook callback."""
    try:
        payload = await request.json()
        order_id = payload.get("data", {}).get("object", {}).get("order_created", {}).get("order_id")
        order_state = payload.get("data", {}).get("object", {}).get("order_created", {}).get("state")
        
        _LOGGER.info("Received JSON payload. Order ID: %s", order_id)
        
        # Check if the order state is "DRAFT"
        if order_state == "DRAFT":
            _LOGGER.info("Order is in DRAFT state. Skipping further processing.")
            return None
        
        # Get the Square API key from data
        api_key = hass.data[DOMAIN]['api_key']
        
        # Make an HTTP request to Square's API using executor
        response = await hass.async_add_executor_job(make_api_request, order_id, api_key)
        
        # Process the response
        if response.status_code == 200:
            order_details = response.json()
            _LOGGER.info("Received JSON payload. Order details: %s", order_details)
            
            # Extract relevant information from order_details
            source_name = order_details.get("order", {}).get("source", {}).get("name", "").upper()
            recipient_name = None
            for fulfillment in order_details.get("order", {}).get("fulfillments", []):
                recipient_info = None
                if "pickup_details" in fulfillment:
                    recipient_info = fulfillment["pickup_details"].get("recipient")
                elif "delivery_details" in fulfillment:
                    recipient_info = fulfillment["delivery_details"].get("recipient")
                if recipient_info:
                    recipient_name = recipient_info.get("display_name").upper()
                    break
            line_items = order_details.get("order", {}).get("line_items", [])
            fulfillment_type = order_details.get("order", {}).get("fulfillments", [{}])[0].get("type", "").upper()
            scheduled_time = order_details.get("order", {}).get("fulfillments", [{}])[0].get("pickup_details", {}).get("pickup_at") or order_details.get("order", {}).get("fulfillments", [{}])[0].get("delivery_details", {}).get("deliver_at")
            total_money = order_details.get("order", {}).get("total_money", {}).get("amount")
            
            # Format the notification message
            message = f"New {source_name} order from {recipient_name} containing:\n\n"
            for item in line_items:
                name = item.get("name")
                quantity = item.get("quantity")
                message += f"{name} x {quantity}\n"
            
            message += "\n"
            if fulfillment_type:
                message += f"{fulfillment_type.capitalize()} is scheduled for "
            
                if scheduled_time:
                    # Parse the string into a naive datetime object
                    scheduled_time = datetime.datetime.strptime(scheduled_time, "%Y-%m-%dT%H:%M:%S.%fZ")

                    # Add timezone info for UTC
                    scheduled_time = scheduled_time.replace(tzinfo=tz.gettz('UTC'))

                    # Convert to local timezone
                    scheduled_time = scheduled_time.astimezone(tz.tzlocal())

                    if scheduled_time.date() == datetime.datetime.now().date():
                        scheduled_time_formatted = scheduled_time.strftime("%I:%M%p") + " today"
                    else:
                        scheduled_time_formatted = scheduled_time.strftime("%I:%M%p on %m/%d/%Y")
                    message += scheduled_time_formatted

            message += f"\n\nhttps://squareup.com/dashboard/orders/overview/{order_id}"
            
            message += f"\n\nThe order total is ${total_money/100:.2f}"
            
            # Send the notification
            await hass.services.async_call("notify", "shop", {"message": message})
            
        else:
            _LOGGER.error("Failed to retrieve order details from Square API. Status code: %s", response.status_code)
        
    except ValueError:
        _LOGGER.error("Received invalid JSON payload")
    
    return None


def make_api_request(order_id, api_key):
    """Make an HTTP request to Square's API."""
    url = f"https://connect.squareup.com/v2/orders/{order_id}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    response = requests.get(url, headers=headers)
    return response
