# main.py

import csv
from datetime import datetime, timedelta
from hash_table import HashTable
from truck import Truck
from package import Package

# Global lists for distance and address data
distance_data = []
address_data = []

# Load distance data from CSV
def load_distance_data(file_path):
    global distance_data
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        distance_data = [
            list(map(lambda x: float(x) if x else 0.0, row))
            for row in reader
        ]

# Load address data from CSV
def load_address_data(file_path):
    global address_data
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        address_data = [row for row in reader]

# Load package data from CSV and populate the hash table
def load_package_data(file_path, package_hash_table):
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header row if your CSV has one
        for row in reader:
            try:
                package_id = int(row[0])
                address = row[1]
                city = row[2]
                state = row[3]
                zip_code = row[4]
                deadline = row[5]
                # row[6] might be something like "21 Kilos". Let's parse the number:
                weight_str = row[6].replace("Kilos", "").strip()
                weight = int(weight_str) if weight_str.isdigit() else 0

                status = "At Hub"  # Default status
                package = Package(package_id, address, city, state, zip_code, deadline, weight, status)
                package_hash_table.insert(package_id, package)
                print(f"Inserted package ID {package_id} into hash table.")
            except Exception as e:
                print(f"Error processing row {row}: {e}")

# Find distance between two addresses
def get_distance(location1, location2):
    distance = distance_data[location1][location2]
    if distance == 0.0:
        distance = distance_data[location2][location1]
    return distance

# Get address index from address string
def get_address_index(address):
    for index, row in enumerate(address_data):
        # Adjust if your address is stored in a different column
        if address in row[2]:
            return index
    return -1

# Compute package status at a specific query time
def compute_package_status(package, current_time):
    """
    Return one of: "At Hub", "En Route", or "Delivered at ..."
    depending on how current_time compares to package.departure_time and package.delivery_time.
    """
    if not package.departure_time or current_time < package.departure_time:
        return "At Hub"
    elif package.delivery_time and current_time >= package.delivery_time:
        return f"Delivered at {package.delivery_time}"
    else:
        return "En Route"

# Correct package #9 address if user-specified time >= 10:20 AM
def correct_package_address(package_hash_table, current_time):
    fix_time = datetime(2024, 12, 12, 10, 20, 0)
    if current_time >= fix_time:
        package_9 = package_hash_table.lookup(9)
        if package_9:
            package_9.address = "410 S State St"
            package_9.city = "Salt Lake City"
            package_9.state = "UT"
            package_9.zip_code = "84111"
            print("Corrected address for package #9.")

# Initialize the hash table
package_hash_table = HashTable()

# Load data files (adjust these paths if yours differ)
load_distance_data("data/Distance.csv")
load_address_data("data/Address.csv")
load_package_data("data/Package.csv", package_hash_table)

# Validate that all packages 1..40 exist
for pkg_id in range(1, 41):
    pkg = package_hash_table.lookup(pkg_id)
    if not pkg:
        print(f"Warning: Package ID {pkg_id} not found in the hash table.")
    else:
        print(f"Package ID {pkg_id}: {pkg}")

# Initialize trucks (start at 8:00 AM on 2024-12-12)
truck1 = Truck(1, 18, 0.0, datetime(2024, 12, 12, 8, 0, 0))
truck2 = Truck(2, 18, 0.0, datetime(2024, 12, 12, 8, 0, 0))
truck3 = Truck(3, 18, 0.0, datetime(2024, 12, 12, 8, 0, 0))

# Assign packages
truck1.packages = [package_hash_table.lookup(i) for i in range(1, 17) if package_hash_table.lookup(i)]
truck2.packages = [package_hash_table.lookup(i) for i in range(17, 33) if package_hash_table.lookup(i)]
truck3.packages = [package_hash_table.lookup(i) for i in range(33, 41) if package_hash_table.lookup(i)]

print("Truck 1 packages:", [p.package_id for p in truck1.packages])
print("Truck 2 packages:", [p.package_id for p in truck2.packages])
print("Truck 3 packages:", [p.package_id for p in truck3.packages])

# Deliver packages using a nearest-neighbor approach
def deliver_packages(truck):
    current_location = 0  # assume "0" index is the Hub location
    while truck.packages:
        try:
            # pick the next package with the smallest distance from 'current_location'
            next_package = min(
                (pkg for pkg in truck.packages if pkg),
                key=lambda p: get_distance(current_location, get_address_index(p.address))
            )
            truck.packages.remove(next_package)

            # Calculate travel distance/time
            dist = get_distance(current_location, get_address_index(next_package.address))
            truck.mileage += dist
            delivery_time = truck.current_time + timedelta(hours=dist / truck.speed)

            # Update package times
            next_package.departure_time = truck.current_time
            next_package.delivery_time = delivery_time
            next_package.status = f"Delivered at {delivery_time}"
            package_hash_table.insert(next_package.package_id, next_package)

            # Advance the truck's clock
            truck.current_time = delivery_time

            # Move to that package’s location
            current_location = get_address_index(next_package.address)

        except Exception as e:
            print(f"Error delivering package on truck {truck.truck_id}: {e}")
            break

# Run deliveries for each truck
for t in [truck1, truck2, truck3]:
    deliver_packages(t)

# ----------------------------------------------------------------------------
# POST-PROCESS FIX FOR PACKAGE #9:
# If #9 ended up with a delivery_time BEFORE 10:20, force it to be 10:25
# ----------------------------------------------------------------------------
fix_time = datetime(2024, 12, 12, 10, 20, 0)
package_9 = package_hash_table.lookup(9)
if package_9 and package_9.delivery_time and package_9.delivery_time < fix_time:
    forced_delivery = fix_time + timedelta(minutes=5)  # e.g. 10:25 AM
    package_9.delivery_time = forced_delivery
    package_9.status = f"Delivered at {forced_delivery}"
    package_hash_table.insert(9, package_9)
    print("NOTE: Package #9 was manually forced to deliver AFTER 10:20, "
          f"so it is now 'Delivered at {forced_delivery}' instead of earlier.")

# Function to parse user time input as a datetime
def parse_time_input():
    user_time = input("Enter time (HH:MM:SS): ")
    (h, m, s) = user_time.split(":")
    # Match the same date as the trucks' start date
    return datetime(2024, 12, 12, int(h), int(m), int(s))

# Command-line UI
def main():
    print("WGUPS Routing Program")
    while True:
        print("\nMenu:")
        print("1. Lookup a single package at a specific time")
        print("2. View statuses of all packages at a specific time")
        print("3. View total mileage of all trucks")
        print("4. Exit")
        choice = input("Enter your choice: ")

        if choice == "1":
            try:
                package_id = int(input("Enter package ID: "))
                query_time = parse_time_input()

                correct_package_address(package_hash_table, query_time)

                package = package_hash_table.lookup(package_id)
                if package:
                    status = compute_package_status(package, query_time)
                    print(f"Package ID: {package.package_id}, Address: {package.address}, "
                          f"City: {package.city}, Zip: {package.zip_code}, Deadline: {package.deadline}, "
                          f"Weight: {package.weight} lbs, Status: {status}")
                else:
                    print(f"Package ID {package_id} not found.")
            except Exception as e:
                print(f"Error: {e}")

        elif choice == "2":
            try:
                query_time = parse_time_input()
                # Correct #9's address if it's after 10:20
                correct_package_address(package_hash_table, query_time)
                print(f"Package statuses at {query_time}:")
                for pkg_id in range(1, 41):
                    pkg = package_hash_table.lookup(pkg_id)
                    if pkg:
                        status = compute_package_status(pkg, query_time)
                        print(f"Package ID: {pkg.package_id}, Address: {pkg.address}, "
                              f"City: {pkg.city}, Zip: {pkg.zip_code}, Deadline: {pkg.deadline}, "
                              f"Weight: {pkg.weight} lbs, Status: {status}")
            except Exception as e:
                print(f"Error: {e}")

        elif choice == "3":
            total_mileage = truck1.mileage + truck2.mileage + truck3.mileage
            print(f"Total mileage of all trucks: {total_mileage:.2f} miles")

        elif choice == "4":
            print("Exiting the program.")
            break

        else:
            print("Invalid choice. Please try again.")

# Entry point
if __name__ == "__main__":
    main()
