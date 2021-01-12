import contextlib
import re

import pandas as pd
import requests

from compass._scrapers.member import CompassPeopleScraper
from compass.utility import maybe_int

normalise_cols = re.compile(r"((?<=[a-z0-9])[A-Z]|(?!^)[A-Z](?=[a-z]))|_([^_])")

# SCRAPER CLASS - 1-1 mapping with compass to minimise calls
# MAIN CLASS - object/properties focused, with abstractions of actual calls
# UTILITY CLASS - get_member_data, get_roles_from_members, etc


class CompassPeople:
    def __init__(self, session: requests.Session):
        self._scraper = CompassPeopleScraper(session)

    def get_roles(self, membership_num: int, keep_non_volunteer_roles: bool = False) -> list:
        response = self._scraper.get_roles_tab(membership_num, keep_non_volunteer_roles)

        role_list = []
        for role_number, role_dict in response.items():
            if not role_dict["can_view_details"]:
                continue

            role_detail = self._scraper.get_roles_detail(role_number)
            details = role_detail["details"]
            hierarchy = role_detail["hierarchy"]
            data = {
                "membership_number": membership_num,
                "role_title": role_dict.get("role_title"),
                "role_start": role_dict.get("role_start"),
                "role_end": role_dict.get("role_end"),
                "role_status": role_dict.get("role_status"),
                "line_manager_number": maybe_int(details.get("line_manager_number")),
                "line_manager": details.get("line_manager"),
                "review_date": details.get("review_date"),
                "organisation": hierarchy.get("organisation"),
                "region": hierarchy.get("region"),
                "county": hierarchy.get("county"),
                "district": hierarchy.get("district"),
                "group": hierarchy.get("group"),
                "section": hierarchy.get("section"),
                "ce_check": details.get("ce_check"),
                "appointment_panel_approval": details.get("appointment_panel_approval"),
                "commissioner_approval": details.get("commissioner_approval"),
                "committee_approval": details.get("committee_approval"),
                "references": details.get("references"),
                **role_detail["getting_started"],
                "training_completion_date": None,
            }
            role_list.append({k: v for k, v in data.items() if v})

        return role_list

    # See getRole in PGS\Needle
    def _roles_tab(self, membership_num: int, keep_non_volunteer_roles: bool = False) -> dict:
        """
        Gets the data from the Role tab in Compass for the specified member.

        Sanitises the data to a common format, and removes Occasional Helper, Network, and PVG roles by default.

        :param membership_num:
        :param keep_non_volunteer_roles:
        :return:
        """
        return self._scraper.get_roles_tab(membership_num, keep_non_volunteer_roles)

    def _training_tab(self, membership_num: int) -> dict:
        """
        Gets training tab data for a given member

        :param membership_num: Compass ID
        :return:
        """
        return self._scraper.get_training_tab(membership_num)

    def _permits_tab(self, membership_num: int) -> list:
        return self._scraper.get_permits_tab(membership_num)


class CompassPeopleUtility(CompassPeople):

    def get_member_data(self, membership_num: int) -> pd.DataFrame:
        """
        Gets Compliance Report data for a specified member

        :param membership_num:
        :return:
        """

        # Columns for the compliance report in order
        # fmt: off
        compliance_columns = [
            "Membership_Number", "Forenames", "Surname", "Known_As", "Email",
            "Role", "Role_Start_Date", "Role_End_Date", "RoleStatus", "Review_date",
            "Country", "Region", "County", "District", "ScoutGroup", "DOB",
            "CE_Check", "AppAdvComm_Approval", "Commissioner_Approval", "Committee_Approval", "References",
            "Essential_Info", "PersonalLearningPlan", "Tools4Role", "GDPR",
            "WoodBadgeReceived", "SafetyTraining", "SafeguardingTraining", "FirstAidTraining"
        ]
        # fmt: on

        roles_data = pd.DataFrame(self._roles_tab(membership_num)).T  # coerce to dataframe
        if roles_data.empty:
            return pd.DataFrame(columns=compliance_columns)

        full_roles_mask = (roles_data["role_status"] != "Closed") & (roles_data["location_id"] > 0)
        open_roles = roles_data.index[full_roles_mask].to_list()
        roles_detail_array = [self._scraper.get_roles_detail(role_number) for role_number in open_roles]
        training_data = self._training_tab(membership_num)  # TODO rename completion date to WoodBadgeReceived
        if training_data["roles"]:
            training_frame = pd.DataFrame(training_data["roles"]).T
            training_ogl = training_data["mandatory"]
            empty_ogl = {"renewal_date": None}
            training_frame["SafetyTraining"] = training_ogl.get("SA", empty_ogl)["renewal_date"]
            training_frame["SafeguardingTraining"] = training_ogl.get("SG", empty_ogl)["renewal_date"]
            training_frame["FirstAidTraining"] = training_ogl.get("FA", empty_ogl)["renewal_date"]

            training_data = training_frame
        else:
            training_data = pd.DataFrame()

        compliance_data = pd.DataFrame(roles_detail_array)
        compliance_data = pd.concat([compliance_data[col].apply(pd.Series) for col in compliance_data], axis=1)
        compliance_data = compliance_data.set_index(["role_number"])
        compliance_data = compliance_data.join(roles_data)
        compliance_data = compliance_data.merge(training_data, how="left", left_index=True, right_index=True)
        compliance_data = compliance_data.reindex(columns=compliance_columns)
        compliance_data.columns = compliance_data.columns.str.replace(normalise_cols, r"_\1\2", regex=True).str.lower()

        compliance_data["membership_number"] = membership_num

        personal_details = self._scraper.get_personal_tab(membership_num)
        for key, value in personal_details.items():
            compliance_data[key] = value

        # # Fill all rows with Mandatory Ongoing Learning data
        # mol_columns = ['safety_training', 'safeguarding_training', 'first_aid_training']
        # mol_data = compliance_data[mol_columns].dropna().iloc[0:1]
        # compliance_data[mol_columns] = compliance_data[mol_columns].fillna(mol_data)
        #
        # date_cols = [
        #     "role_start", "role_end",
        #     "ce_check", "review_date", "essential_info", "personal_learning_plan", "tools4_role", "gdpr",
        #     "wood_badge_received", "safety_training", "safeguarding_training", "first_aid_training"
        # ]
        #
        # # Covert to dd/mm/YYYY format, and get values where it isn't 'NaT', as NaT gets stringifyed
        # for col in date_cols:
        #     compliance_data[col] = pd.to_datetime(compliance_data[col])\
        #         .dt.strftime('%d/%m/%Y')\
        #         .str.replace("NaT", "", regex=False)

        text_cols = compliance_data.columns[compliance_data.dtypes == "object"]
        for col in text_cols:
            compliance_data[col] = compliance_data[col].str.strip()

        return compliance_data

    def get_roles_from_members(self, compass_unit_id: int, member_numbers: pd.Series):
        with contextlib.suppress(FileNotFoundError):
            # Attempt to see if the roles table has been fetched already and is on the local system
            roles_table = pd.read_csv(f"all_roles-{compass_unit_id}.csv")
            if roles_table:
                return roles_table

        member_numbers = member_numbers.drop_duplicates().to_list()
        roles_list = []
        for member_number in member_numbers:
            try:
                roles_data = pd.DataFrame(self._roles_tab(member_number)).T  # coerce to dataframe
                roles_list.append(roles_data)
            except Exception as e:
                with open("error_roles.txt", "a") as f:
                    f.write(f"Member Number: {member_number}\n")
                    f.write(f"Exception: {e}\n\n")
        roles_table = pd.concat(roles_list, sort=False)
        roles_table.to_csv(f"all_roles-{compass_unit_id}.csv", index=False, encoding="utf-8-sig")
        return roles_table
