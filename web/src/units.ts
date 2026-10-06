/** The API and storage speak meters. Everything a person reads or picks is in US units. */
export const METERS_PER_MILE = 1609.344;
export const FEET_PER_METER = 3.28084;

export const metersToMiles = (meters: number): number => meters / METERS_PER_MILE;
export const milesToMeters = (miles: number): number => Math.round(miles * METERS_PER_MILE);
