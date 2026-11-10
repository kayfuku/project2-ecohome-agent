"""
Tools for EcoHome Energy Advisor Agent
"""
import os
import json
import random
from datetime import datetime, timedelta
from typing import Dict, Any
from langchain_core.tools import tool
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from models.energy import DatabaseManager

# Initialize database manager
db_manager = DatabaseManager()


# TODO: Implement get_weather_forecast tool
@tool
def get_weather_forecast(location: str, days: int = 3) -> Dict[str, Any]:
    """
    Get weather forecast for a specific location and number of days.

    Args:
        location (str): Location to get weather for (e.g., "San Francisco, CA")
        days (int): Number of days to forecast (1-7)

    Returns:
        Dict[str, Any]: Weather forecast data including temperature, conditions, and solar irradiance
        E.g:
        forecast = {
            "location": ...,
            "forecast_days": ...,
            "current": {
                "temperature_c": ...,
                "condition": ...,  # "sunny" | "partly_cloudy" | "cloudy" | "rainy"
                "humidity": ...,
                "wind_speed": ...
            },
            "hourly": [
                {
                    "hour": ..., # for hour in range(24)
                    "temperature_c": ...,
                    "condition": ...,
                    "solar_irradiance": ...,
                    "humidity": ...,
                    "wind_speed": ...
                },
            ]
        }
    """
    # Mock weather API
    import math

    days = max(1, min(int(days), 7))
    # Same weather model as the solar_generation data in 01_db_setup.ipynb:
    # condition -> (irradiance multiplier, probability of occurring on a day)
    weather_conditions = {
        "sunny": {"multiplier": 1.0, "probability": 0.4},
        "partly_cloudy": {"multiplier": 0.6, "probability": 0.3},
        "cloudy": {"multiplier": 0.3, "probability": 0.2},
        "rainy": {"multiplier": 0.1, "probability": 0.1},
    }
    conditions = list(weather_conditions.keys())
    weights = [w["probability"] for w in weather_conditions.values()]
    # W/m^2 at noon under clear sky (same as the DB, where 800 W/m^2 ~ 5 kWh)
    peak_irradiance = 800.0

    now = datetime.now()
    start_date = now.date()

    hourly = []
    for day_offset in range(days):
        date = start_date + timedelta(days=day_offset)
        # Seed by location + date so repeated calls return the same forecast
        rng = random.Random(f"{location.strip().lower()}|{date.isoformat()}")

        # Daily baseline: average temperature and the day's condition
        base_temp = rng.uniform(15, 25)
        condition = rng.choices(conditions, weights=weights)[0]
        multiplier = weather_conditions[condition]["multiplier"]

        for hour in range(24):
            # Temperature peaks around 15:00 and bottoms out around 03:00
            temp = base_temp + 4 * math.sin((hour - 9) / 24 * 2 * math.pi)
            temp += rng.uniform(-1, 1)

            # Solar irradiance: linear curve peaking at noon, daylight 6-18 (as in the DB)
            if 6 <= hour <= 18:
                hour_factor = 1 - abs(hour - 12) / 6
            else:
                hour_factor = 0.0
            solar_irradiance = peak_irradiance * hour_factor * multiplier

            hourly.append({
                "date": date.isoformat(),
                "hour": hour,
                "temperature_c": round(temp, 1),
                "condition": condition,
                "solar_irradiance": round(solar_irradiance, 1),  # W/m^2
                "humidity": rng.randint(70, 95) if condition == "rainy" else rng.randint(40, 80),  # %
                "wind_speed": round(rng.uniform(0, 25), 1),  # km/h
            })

    current_hour = hourly[now.hour]
    forecast = {
        "location": location,
        "forecast_days": days,
        "current": {
            "temperature_c": current_hour["temperature_c"],
            "condition": current_hour["condition"],
            "humidity": current_hour["humidity"],
            "wind_speed": current_hour["wind_speed"],
        },
        "hourly": hourly,
    }

    return forecast


# TODO: Implement get_electricity_prices tool
@tool
def get_electricity_prices(date: str = None) -> Dict[str, Any]:
    """
    Get electricity prices for a specific date or current day.

    Args:
        date (str): Date in YYYY-MM-DD format (defaults to today)

    Returns:
        Dict[str, Any]: Electricity pricing data with hourly rates
        E.g:
        prices = {
            "date": ...,
            "pricing_type": "time_of_use",
            "currency": "USD",
            "unit": "per_kWh",
            "hourly_rates": [
                {
                    "hour": .., # for hour in range(24)
                    "rate": ..,
                    "period": ..,
                    "demand_charge": ...
                }
            ]
        }
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    # Mock electricity pricing - in real implementation, this would call a pricing API
    # Use a base price per kWh
    # Then generate hourly rates with peak/off-peak pricing
    # Peak normally between 6 and 22...
    # demand_charge should be 0 if off-peak
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return {"error": f"Invalid date format: {date}. Use YYYY-MM-DD."}

    # Seed by date so repeated calls for the same date return the same prices
    rng = random.Random(date)

    # Same rates as cost_usd in 01_db_setup.ipynb (peak 0.15, off-peak 0.10)
    base_price = 0.10  # USD per kWh (off-peak)
    peak_multiplier = 1.5  # -> 0.15 USD per kWh during peak
    off_peak_multiplier = 1.0
    peak_demand_charge = 0.02  # USD per kWh, applied only during peak

    hourly_rates = []
    for hour in range(24):
        is_peak = 6 <= hour < 22
        multiplier = peak_multiplier if is_peak else off_peak_multiplier
        # Small hourly fluctuation (+/-3%)
        rate = base_price * multiplier * rng.uniform(0.97, 1.03)

        hourly_rates.append({
            "hour": hour,
            "rate": round(rate, 4),
            "period": "peak" if is_peak else "off_peak",
            "demand_charge": peak_demand_charge if is_peak else 0.0,
        })

    prices = {
        "date": date,
        "pricing_type": "time_of_use",
        "currency": "USD",
        "unit": "per_kWh",
        "hourly_rates": hourly_rates,
    }

    return prices


@tool
def query_energy_usage(start_date: str, end_date: str, device_type: str = None) -> Dict[str, Any]:
    """
    Query energy usage data from the database for a specific date range.

    Args:
        start_date (str): Start date in YYYY-MM-DD format
        end_date (str): End date in YYYY-MM-DD format
        device_type (str): Optional device type filter (e.g., "EV", "HVAC", "appliance")

    Returns:
        Dict[str, Any]: Energy usage data with consumption details
    """
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)

        records = db_manager.get_usage_by_date_range(start_dt, end_dt)

        if device_type:
            records = [r for r in records if r.device_type == device_type]

        usage_data = {
            "start_date": start_date,
            "end_date": end_date,
            "device_type": device_type,
            "total_records": len(records),
            "total_consumption_kwh": round(sum(r.consumption_kwh for r in records), 2),
            "total_cost_usd": round(sum(r.cost_usd or 0 for r in records), 2),
            "records": []
        }

        for record in records:
            usage_data["records"].append({
                "timestamp": record.timestamp.isoformat(),
                "consumption_kwh": record.consumption_kwh,
                "device_type": record.device_type,
                "device_name": record.device_name,
                "cost_usd": record.cost_usd
            })

        return usage_data
    except Exception as e:
        return {"error": f"Failed to query energy usage: {str(e)}"}


@tool
def query_solar_generation(start_date: str, end_date: str) -> Dict[str, Any]:
    """
    Query solar generation data from the database for a specific date range.

    Args:
        start_date (str): Start date in YYYY-MM-DD format
        end_date (str): End date in YYYY-MM-DD format

    Returns:
        Dict[str, Any]: Solar generation data with production details
    """
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)

        records = db_manager.get_generation_by_date_range(start_dt, end_dt)

        generation_data = {
            "start_date": start_date,
            "end_date": end_date,
            "total_records": len(records),
            "total_generation_kwh": round(sum(r.generation_kwh for r in records), 2),
            "average_daily_generation": round(sum(r.generation_kwh for r in records) / max(1, (end_dt - start_dt).days), 2),
            "records": []
        }

        for record in records:
            generation_data["records"].append({
                "timestamp": record.timestamp.isoformat(),
                "generation_kwh": record.generation_kwh,
                "weather_condition": record.weather_condition,
                "temperature_c": record.temperature_c,
                "solar_irradiance": record.solar_irradiance
            })

        return generation_data
    except Exception as e:
        return {"error": f"Failed to query solar generation: {str(e)}"}


@tool
def get_recent_energy_summary(hours: int = 24) -> Dict[str, Any]:
    """
    Get a summary of recent energy usage and solar generation.

    Args:
        hours (int): Number of hours to look back (default 24)

    Returns:
        Dict[str, Any]: Summary of recent energy data
    """
    try:
        usage_records = db_manager.get_recent_usage(hours)
        generation_records = db_manager.get_recent_generation(hours)

        summary = {
            "time_period_hours": hours,
            "usage": {
                "total_consumption_kwh": round(sum(r.consumption_kwh for r in usage_records), 2),
                "total_cost_usd": round(sum(r.cost_usd or 0 for r in usage_records), 2),
                "device_breakdown": {}
            },
            "generation": {
                "total_generation_kwh": round(sum(r.generation_kwh for r in generation_records), 2),
                "average_weather": "sunny" if generation_records else "unknown"
            }
        }

        # Calculate device breakdown
        for record in usage_records:
            device = record.device_type or "unknown"
            if device not in summary["usage"]["device_breakdown"]:
                summary["usage"]["device_breakdown"][device] = {
                    "consumption_kwh": 0,
                    "cost_usd": 0,
                    "records": 0
                }
            summary["usage"]["device_breakdown"][device]["consumption_kwh"] += record.consumption_kwh
            summary["usage"]["device_breakdown"][device]["cost_usd"] += record.cost_usd or 0
            summary["usage"]["device_breakdown"][device]["records"] += 1

        # Round the breakdown values
        for device_data in summary["usage"]["device_breakdown"].values():
            device_data["consumption_kwh"] = round(
                device_data["consumption_kwh"], 2)
            device_data["cost_usd"] = round(device_data["cost_usd"], 2)

        return summary
    except Exception as e:
        return {"error": f"Failed to get recent energy summary: {str(e)}"}


@tool
def search_energy_tips(query: str, max_results: int = 5) -> Dict[str, Any]:
    """
    Search for energy-saving tips and best practices using RAG.

    Args:
        query (str): Search query for energy tips
        max_results (int): Maximum number of results to return

    Returns:
        Dict[str, Any]: Relevant energy tips and best practices
    """
    try:
        # Initialize vector store if it doesn't exist
        persist_directory = "data/vectorstore"
        if not os.path.exists(persist_directory):
            os.makedirs(persist_directory)

        # Load documents if vector store doesn't exist
        if not os.path.exists(os.path.join(persist_directory, "chroma.sqlite3")):
            # Load documents
            documents = []
            document_paths = [
                "data/documents/tip_device_best_practices.txt",
                "data/documents/tip_energy_savings.txt",
                "data/documents/tip_hvac_optimization.txt",
                "data/documents/tip_smart_home_automation.txt",
                "data/documents/tip_renewable_energy_integration.txt",
                "data/documents/tip_seasonal_energy_management.txt",
                "data/documents/tip_energy_storage_optimization.txt",
            ]
            for doc_path in document_paths:
                if os.path.exists(doc_path):
                    loader = TextLoader(doc_path)
                    docs = loader.load()
                    documents.extend(docs)

            # Split documents
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=1000, chunk_overlap=200)
            splits = text_splitter.split_documents(documents)

            # Create vector store
            embeddings = OpenAIEmbeddings(
                base_url="https://openai.vocareum.com/v1",
                api_key=os.getenv("VOCAREUM_API_KEY")
            )
            vectorstore = Chroma.from_documents(
                documents=splits,
                embedding=embeddings,
                persist_directory=persist_directory
            )
        else:
            # Load existing vector store
            embeddings = OpenAIEmbeddings(
                base_url="https://openai.vocareum.com/v1",
                api_key=os.getenv("VOCAREUM_API_KEY")
            )
            vectorstore = Chroma(
                persist_directory=persist_directory,
                embedding_function=embeddings
            )

        # Search for relevant documents
        docs = vectorstore.similarity_search(query, k=max_results)

        results = {
            "query": query,
            "total_results": len(docs),
            "tips": []
        }

        for i, doc in enumerate(docs):
            results["tips"].append({
                "rank": i + 1,
                "content": doc.page_content,
                "source": doc.metadata.get("source", "unknown"),
                "relevance_score": "high" if i < 2 else "medium" if i < 4 else "low"
            })

        return results
    except Exception as e:
        return {"error": f"Failed to search energy tips: {str(e)}"}


@tool
def calculate_energy_savings(device_type: str, current_usage_kwh: float,
                             optimized_usage_kwh: float, price_per_kwh: float = 0.12,
                             optimized_price_per_kwh: float = None) -> Dict[str, Any]:
    """
    Calculate energy and cost savings from an optimization. ALWAYS use this tool to
    produce any savings figure (kWh, $, %, daily/monthly/annual) instead of doing the
    arithmetic yourself.

    Supports two kinds of optimization (or both at once):
      - Usage reduction: fewer kWh at the same price
        (e.g. pool pump 10 -> 6 kWh/day: current=10, optimized=6, price=<rate>).
      - Load shifting: the same kWh moved to a cheaper time
        (e.g. EV 30 kWh/day moved from peak to off-peak: current=30, optimized=30,
        price_per_kwh=<peak rate>, optimized_price_per_kwh=<off-peak rate>).

    All kWh inputs must be DAILY values; monthly (x30) and annual (x365) figures are
    derived from them. Convert weekly/monthly totals to daily before calling.

    Args:
        device_type (str): Type of device being optimized (e.g. "EV", "HVAC", "pool_pump")
        current_usage_kwh (float): Current daily energy usage in kWh
        optimized_usage_kwh (float): Daily energy usage in kWh after the optimization
        price_per_kwh (float): Current price per kWh in USD; pass the real rate from
            get_electricity_prices (default 0.12)
        optimized_price_per_kwh (float): Price per kWh after the optimization, for load
            shifting (e.g. the off-peak rate). Defaults to `price_per_kwh`.

    Returns:
        Dict[str, Any]: Daily, monthly and annual kWh and USD savings plus percentages,
            or {"error": ...} for invalid inputs
    """
    if current_usage_kwh < 0 or optimized_usage_kwh < 0:
        return {"error": "Usage values must be non-negative kWh."}
    if optimized_price_per_kwh is None:
        optimized_price_per_kwh = price_per_kwh
    if price_per_kwh < 0 or optimized_price_per_kwh < 0:
        return {"error": "Prices must be non-negative USD/kWh."}

    current_cost_usd = current_usage_kwh * price_per_kwh
    optimized_cost_usd = optimized_usage_kwh * optimized_price_per_kwh

    savings_kwh = current_usage_kwh - optimized_usage_kwh
    savings_usd = current_cost_usd - optimized_cost_usd
    savings_percentage = (savings_kwh / current_usage_kwh) * \
        100 if current_usage_kwh > 0 else 0
    cost_savings_percentage = (savings_usd / current_cost_usd) * \
        100 if current_cost_usd > 0 else 0

    return {
        "device_type": device_type,
        "current_usage_kwh": current_usage_kwh,
        "optimized_usage_kwh": optimized_usage_kwh,
        "price_per_kwh": price_per_kwh,
        "optimized_price_per_kwh": optimized_price_per_kwh,
        "current_daily_cost_usd": round(current_cost_usd, 2),
        "optimized_daily_cost_usd": round(optimized_cost_usd, 2),
        "savings_kwh": round(savings_kwh, 2),
        "savings_usd": round(savings_usd, 2),
        "savings_percentage": round(savings_percentage, 1),
        "cost_savings_percentage": round(cost_savings_percentage, 1),
        "monthly_savings_kwh": round(savings_kwh * 30, 2),
        "monthly_savings_usd": round(savings_usd * 30, 2),
        "annual_savings_kwh": round(savings_kwh * 365, 2),
        "annual_savings_usd": round(savings_usd * 365, 2),
    }


TOOL_KIT = [
    get_weather_forecast,
    get_electricity_prices,
    query_energy_usage,
    query_solar_generation,
    get_recent_energy_summary,
    search_energy_tips,
    calculate_energy_savings
]
