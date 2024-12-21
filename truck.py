from datetime import datetime

class Truck:
    def __init__(
            self,
            truck_id,            # e.g. 1, 2, or 3
            speed,               # in miles/hour, e.g. 18
            mileage=0.0,         # total mileage driven
            start_time=None,     # datetime object for when the truck starts
            capacity=16,         # optional: how many packages it can hold
            address="HUB",       # optional: current address (e.g. "4001 South 700 East")
    ):
        """
        Truck object for routing packages.
        :param truck_id: int       - Unique ID for the truck
        :param speed: float        - Truck's speed in mph (e.g. 18)
        :param mileage: float      - Initial mileage (default 0.0)
        :param start_time: datetime or None - If None, defaults to now()
        :param capacity: int       - (Optional) carrying capacity for packages
        :param address: str        - (Optional) current or starting address
        """
        self.truck_id = truck_id
        self.speed = speed
        self.mileage = mileage
        self.current_time = start_time or datetime.now()
        self.capacity = capacity
        self.address = address
        self.packages = []  # Will hold Package objects

    def __str__(self):
        """
        Return a string representation of the Truck object.
        """
        return (
            f"Truck ID: {self.truck_id}, Speed: {self.speed} mph, "
            f"Mileage: {self.mileage:.2f} miles, Capacity: {self.capacity}, "
            f"Address: {self.address}, Current Time: {self.current_time}, "
            f"Packages Loaded: {len(self.packages)}"
        )

