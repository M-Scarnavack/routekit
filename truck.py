from datetime import datetime

class Truck:
    def __init__(self, truck_id, speed, mileage=0, start_time=None):
        """
        Initialize a Truck object.
        """
        self.truck_id = truck_id
        self.speed = speed  # in miles per hour
        self.mileage = mileage
        self.current_time = start_time or datetime.now()
        self.packages = []

    def __str__(self):
        """Return a string representation of the Truck object."""
        return (f"Truck ID: {self.truck_id}, Speed: {self.speed} mph, Mileage: {self.mileage:.2f} miles, "
                f"Current Time: {self.current_time}, Packages Loaded: {len(self.packages)}")
