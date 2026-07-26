import unittest

from agent_nodes.executor_agent import extract_station_codes


class TrainQueryParsingTests(unittest.TestCase):
    def test_extracts_codes_from_12306_dict_response(self):
        station_result = (
            '{"上海":{"station_code":"SHH","station_name":"上海"},'
            '"苏州":{"station_code":"SZH","station_name":"苏州"}}'
        )

        from_code, to_code = extract_station_codes(station_result, "上海", "苏州")

        self.assertEqual(from_code, "SHH")
        self.assertEqual(to_code, "SZH")


if __name__ == "__main__":
    unittest.main()
