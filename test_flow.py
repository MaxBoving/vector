from src.core.database import init_db, save_object, get_ceo_preferences, get_company_state
from src.core.models import CompanyState, CEOPreferences, TonePreference
from src.core.brain import ExecutiveBrain
from datetime import datetime

def run_test():
    print("--- Initializing Mock agenticMIND Environment ---")
    init_db()
    
    # 1. Create or Get Fake CEO Preferences
    ceo_id = "ceo_001"
    ceo_prefs = get_ceo_preferences(ceo_id)
    if not ceo_prefs:
        ceo_prefs = CEOPreferences(
            ceo_id=ceo_id,
            preferred_tone=TonePreference.ANALYTIC,
            decision_velocity=8
        )
        save_object(ceo_prefs)
    
    # 2. Create or Get Fake Company State
    company_name = "InnovateCorp"
    company_state = get_company_state(company_name)
    if not company_state:
        company_state = CompanyState(
            company_name=company_name,
            last_updated=datetime.now().isoformat(),
            revenue_segmentation={"North America": 50000000, "EMEA": 30000000, "APAC": 20000000},
            capital_position={"Cash on Hand": 15000000, "Burn Rate": 2000000},
            strategic_initiatives=[
                {"name": "AI Transformation", "owner": "CTO", "status": "In Progress", "priority": 1}
            ]
        )
        save_object(company_state)
    
    print("--- Running Executive Brain Synthesis ---")
    brain = ExecutiveBrain(ceo_id=ceo_id, company_name=company_name)
    
    # Test Strategic Query
    query = "Should we accelerate our APAC expansion given our current capital position?"
    result = brain.synthesize(query)
    
    print(f"\nCEO Request: {query}")
    print(f"Executive Brain Output: {result}")

if __name__ == "__main__":
    run_test()
