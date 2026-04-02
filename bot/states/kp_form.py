from aiogram.fsm.state import State, StatesGroup


class KPForm(StatesGroup):
    """FSM for commercial proposal (КП) creation — 9 data steps + preview."""

    org_name = State()        # Step 1: Organisation name
    inn = State()             # Step 2: ИНН (10 or 12 digits)
    kpp = State()             # Step 3: КПП (9 digits)
    address = State()         # Step 4: Legal address
    contact_person = State()  # Step 5: Contact person full name
    contact_info = State()    # Step 6: Phone / email
    sample_location = State()  # Step 7: Factual sample location
    research_deadline = State()  # Step 8: Optional research deadline
    sample_return = State()  # Step 9: Return unused sample part
    preview = State()         # Preview + confirm / edit / cancel
